# -*- coding: utf-8 -*-
"""評審團.py — 三層歌曲評審整合器

用法: python 評審團.py 歌曲檔或連結
三種輸入:
1. SUNO 連結(https://suno.com/song/... 或 /s/ 短連結)→ 自動下載歌+抓歌詞
2. YouTube 連結 → 自動下載歌(需 yt-dlp+ffmpeg);⚠️ 抓不到歌詞,請另給
3. 本機音檔路徑(歌詞另給)/ 直接 mp3 連結
第一層 物理技術 = song_scorer(.venv)
第二層 美學情感 = SongEval 五維(.venv-ml)+ Audiobox 四軸(.venv-ml)
第三層 詞曲文本 = LLM 在對話裡評(本程式不做)
輸出: 歌名_評審團.json + 主控台摘要
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

_WIN = sys.platform == "win32"

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent.resolve()
ENV = {**os.environ, "PYTHONUTF8": "1"}

SONGEVAL_LABELS = {
    "Coherence": "整體連貫性", "Musicality": "整體音樂性",
    "Memorability": "記憶點", "Clarity": "結構清晰度", "Naturalness": "人聲自然度",
}
AUDIOBOX_LABELS = {
    "PQ": "製作品質", "PC": "製作複雜度", "CE": "內容感染力", "CU": "內容實用性",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_UUID_RE = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"


def _follow_short_link(url):
    """SUNO 短連結(/s/xxxx)只轉址一次就會露出帶 UUID 的正式網址。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=30)
        return resp.geturl()
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if loc:
            return urllib.parse.urljoin(url, loc)
        return url


def fetch_suno_meta(uuid):
    """抓 SUNO 歌曲頁,取正式歌名與歌詞(埋在頁面 prompt 欄位)。失敗回傳 (None, None)。"""
    try:
        req = urllib.request.Request(
            f"https://suno.com/song/{uuid}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None, None
    title = None
    mt = re.search(r"<title>(.*?)\s+by\s", html)
    if mt:
        title = mt.group(1).strip().strip("《》〈〉\"' ")

    def decode_js(s):
        try:
            return json.loads('"' + s + '"')
        except Exception:
            return None

    def lyric_score(t):
        """0=不是歌詞;2=有段落標記(最可信);1=多行文字。先擋網頁程式碼雜訊。"""
        if not t or len(t) < 60:
            return 0
        if '"$"' in t or "_next/static" in t or '"src":' in t or '{"children"' in t:
            return 0
        if re.search(r"\[(intro|verse|chorus|bridge|hook|pre[- ]?chorus|outro)", t, re.I) or "【" in t:
            return 2
        return 1 if t.count("\n") >= 6 else 0

    candidates = []
    # 策略一:prompt 欄位(雙層 JSON 逸出,自訂歌詞模式)
    idx = html.find('\\"prompt\\":\\"')
    if idx >= 0:
        peeled = re.sub(r"\\(.)", r"\1", html[idx:idx + 60000])
        m = re.search(r'"prompt":"((?:[^"\\]|\\.)*)"', peeled, re.S)
        if m:
            d = decode_js(m.group(1))
            if d:
                candidates.append(d.strip())
    # 策略二:Next.js flight 推送字串(單層逸出,新版頁面)
    for m in re.finditer(r'\.push\(\[\d+,"((?:[^"\\]|\\.)*)"', html, re.S):
        d = decode_js(m.group(1))
        if d:
            candidates.append(d.strip())

    best = max(((lyric_score(c), len(c), c) for c in candidates), default=(0, 0, None))
    lyrics = best[2] if best[0] > 0 else None
    return title, lyrics


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def _safe_name(s):
    """檔名安全化:去非法字元、控空白、擋空名與 Windows 保留名。"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (s or "").strip())[:60].strip(" .")
    if not s:
        return "untitled"
    if s.upper().split(".")[0] in _WIN_RESERVED:
        return "_" + s
    return s


def _venv_py(venv):
    """venv 內的 python(跨平台);venv 不存在(如 HF Space/單一環境)則退回當前直譯器。"""
    p = BASE / venv / ("Scripts/python.exe" if _WIN else "bin/python")
    return str(p) if p.exists() else sys.executable


def _venv_exe(venv, name):
    """venv 內的 CLI 執行檔(跨平台);venv 不存在則退回當前環境同名 console script,再退回 PATH。"""
    p = BASE / venv / (f"Scripts/{name}.exe" if _WIN else f"bin/{name}")
    if p.exists():
        return str(p)
    alt = Path(sys.executable).parent / (f"{name}.exe" if _WIN else name)
    return str(alt) if alt.exists() else name


def _run_stage(cmd, cwd, label):
    """跑一個評分子程序;失敗時印出工具名+stderr 尾段+自救提示再退出。"""
    try:
        return subprocess.run(cmd, cwd=str(cwd), env=ENV, check=True,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        sys.exit(f"✗ {label}:找不到執行檔 `{cmd[0]}`。\n"
                 f"→ 對應的 venv 可能沒建好(用 uv 建 .venv / .venv-ml)。")
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or e.stdout or "").strip()[-600:]
        sys.exit(f"✗ {label} 失敗(exit {e.returncode})。\n{tail}\n"
                 f"→ 檢查:venv 依賴是否裝齊(uv pip install)、音檔是否可讀、記憶體/GPU 是否足夠。")


def _last_json(text):
    """從子程序 stdout 取最後一行合法 JSON(容忍前面夾雜 log/warning);單行找不到就整段當一個 JSON。"""
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    try:
        return json.loads((text or "").strip())
    except Exception:
        raise ValueError("子程序沒有輸出可解析的 JSON(可能崩潰或版本不合)")


def _unique_stem(base):
    """在 下載/ 內找不撞名的 stem(同名重抽/多版自動加 v2/v3…,不覆蓋)。"""
    dl = BASE / "下載"
    stem, k = base, 2
    while (dl / f"{stem}.mp3").exists():
        stem = f"{base} v{k}"
        k += 1
    return stem


def _is_youtube(url):
    return bool(re.search(r"(?:youtube\.com|youtu\.be)", url, re.I))


def _yt_run(extra):
    """呼叫 yt-dlp:用跑本程式的同一個直譯器 -m yt_dlp(開源時 pip 裝進 venv 即通用)。"""
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-playlist", *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _download_youtube(url):
    """YT 連結 → 下載 bestaudio 轉 mp3 到 下載\。YT 抓不到歌詞,需使用者另給。"""
    dl_dir = BASE / "下載"
    dl_dir.mkdir(exist_ok=True)
    if _yt_run(["--version"]).returncode != 0:
        sys.exit("找不到 yt-dlp——YouTube 輸入需要它:先安裝(uv pip install yt-dlp)+ 確認 ffmpeg 可用;\n"
                 "或改用方式 3:自行下載 YT 音訊成檔,再把檔案路徑給我。")
    r = _yt_run(["--skip-download", "--print", "%(title)s", url])
    title = (r.stdout.strip().splitlines()[-1].strip()
             if r.returncode == 0 and r.stdout.strip() else "youtube_audio")
    stem = _unique_stem(_safe_name(title))
    print(f"⬇ 從 YouTube 下載中: {title}")
    r2 = _yt_run(["-x", "--audio-format", "mp3", "--audio-quality", "0",
                  "-o", str(dl_dir / f"{stem}.%(ext)s"), url])
    mp3 = dl_dir / f"{stem}.mp3"
    if r2.returncode != 0 or not mp3.exists():
        sys.exit(f"YT 下載失敗:{(r2.stderr or '')[-400:]}\n"
                 f"(需 yt-dlp+ffmpeg;私人/受限影片抓不到,請改用方式 3:自行下載成檔再給)")
    print(f"已存: {mp3}")
    print("📝 YouTube 無法自動抓歌詞——請另外提供歌詞(貼文字,或給 .txt 路徑)")
    return mp3


def resolve_input(arg):
    """本機路徑直接用;SUNO/YouTube 連結、直連 mp3 先下載到 下載/ 再評。"""
    if not re.match(r"^https?://", arg, re.I):
        p = Path(arg).resolve()
        if not p.exists():
            sys.exit(f"找不到檔案: {p}")
        if not str(p).endswith((".wav", ".mp3")):
            fixed = Path(tempfile.mkdtemp(prefix="song_jury_up_")) / (_safe_name(p.stem or "upload") + ".mp3")
            shutil.copy(p, fixed)
            print(f"📎 上傳檔補正音檔副檔名: {fixed}")
            return fixed
        return p
    if _is_youtube(arg):
        return _download_youtube(arg)
    lyrics = None
    uuid = re.search(_UUID_RE, arg)
    if not uuid and "suno.com" in arg.lower():
        arg = _follow_short_link(arg)
        uuid = re.search(_UUID_RE, arg)
    if arg.lower().split("?")[0].endswith(".mp3"):
        url = arg
        base = _safe_name(Path(urllib.parse.urlparse(arg).path).stem or "download")
        name = f"{_unique_stem(base)}.mp3"
    elif uuid:
        url = f"https://cdn1.suno.ai/{uuid.group(1)}.mp3"
        title, lyrics = fetch_suno_meta(uuid.group(1))
        base = _safe_name(title) if title else f"suno_{uuid.group(1)[:8]}"
        name = f"{_unique_stem(base)}.mp3"
        if not lyrics:
            print("📝 頁面抓不到歌詞(可能純音樂或頁面改版),請手動提供")
    else:
        sys.exit("看不懂的連結。請給 SUNO 歌曲頁連結(https://suno.com/song/...)或直接的 mp3 連結")
    dl_dir = BASE / "下載"
    dl_dir.mkdir(exist_ok=True)
    dest = dl_dir / name
    part = dest.with_name(dest.name + ".part")
    print(f"⬇ 從 SUNO 下載中: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        part.unlink(missing_ok=True)
        sys.exit(f"下載失敗(HTTP {e.code})。歌曲可能不是「公開」狀態——"
                 f"私人歌曲請先在 SUNO 網站下載,再用方式 3(直接給檔)評。")
    except Exception as e:
        part.unlink(missing_ok=True)
        sys.exit(f"下載失敗:{type(e).__name__}: {e}(網路問題或連結失效)")
    if part.stat().st_size < 10240:
        part.unlink(missing_ok=True)
        sys.exit("下載到的檔案過小,不像有效音檔(可能是私人歌、連結失效或被擋)。")
    part.replace(dest)
    if lyrics:
        res_dir = dl_dir / f"{dest.stem}_評分結果"
        res_dir.mkdir(parents=True, exist_ok=True)
        (res_dir / f"{dest.stem}_歌詞.txt").write_text(lyrics + "\n", encoding="utf-8")
        print(f"📝 歌詞已自動抓取: {res_dir / (dest.stem + '_歌詞.txt')}")
    print(f"已存: {dest}\n")
    return dest


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python 評審團.py <歌曲檔路徑 或 SUNO/YouTube 連結>\n"
                 " 含空白的路徑請用引號括起。")
    song = resolve_input(sys.argv[1])

    print(f"🎵 評審對象: {song.name}\n")

    # ── 第一層: 物理技術 ──
    print("[1/3] 物理技術評分(song_scorer)...")
    phys_json = song.with_name(song.stem + "_評分.json")
    _run_stage([_venv_py(".venv"), str(BASE / "song_scorer.py"),
                str(song), "--json", str(phys_json)],
               cwd=BASE, label="物理技術(song_scorer)")
    physical = json.loads(phys_json.read_text(encoding="utf-8"))
    phys_json.unlink()

    # ── 第二層 A: SongEval 五維美學 ──
    print("[2/3] SongEval 美學評分(音樂人訓練模型)...")
    tmp_out = Path(tempfile.mkdtemp(prefix="_songeval_", dir=BASE))
    try:
        _run_stage([_venv_py(".venv-ml"), "eval.py", "-i", str(song), "-o", str(tmp_out)],
                   cwd=BASE / "SongEval", label="SongEval 美學")
        se_raw = json.loads((tmp_out / "result.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)
    songeval = list(se_raw.values())[0]

    # ── 第二層 B: Audiobox 四軸 ──
    print("[3/3] Audiobox 美學評分(Meta 模型)...")
    tmp_lst = BASE / f"_tmp_audiobox_{os.getpid()}.jsonl"
    tmp_lst.write_text(json.dumps({"path": str(song)}) + "\n", encoding="utf-8")
    try:
        p = _run_stage([_venv_exe(".venv-ml", "audio-aes"), str(tmp_lst), "--batch-size", "1"],
                       cwd=BASE, label="Audiobox 美學")
        audiobox = _last_json(p.stdout)
    finally:
        tmp_lst.unlink(missing_ok=True)

    # ── 整合輸出 ──
    _lyr_f = song.parent / f"{song.stem}_評分結果" / f"{song.stem}_歌詞.txt"
    _fetched = _lyr_f.read_text(encoding="utf-8").strip() if _lyr_f.exists() else ""
    _audio_hash = hashlib.sha256(song.read_bytes()).hexdigest()[:16]
    merged = {
        "file": song.name,
        "audio_hash": _audio_hash,
        "layer1_physical": physical,
        "layer2_songeval_1to5": songeval,
        "layer2_audiobox_1to10": audiobox,
        "layer3_lyrics": "由 LLM 評(把歌詞丟給評審說「評詞」)",
        "fetched_lyrics": _fetched,
    }
    out_path = song.with_name(song.stem + "_評審團.json")
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    se_avg = sum(songeval.values()) / len(songeval)
    print()
    print("=" * 54)
    print(" 評審團總表")
    print("=" * 54)
    print(f"【物理技術】 {physical['scores']['total']} / 100(等級 {physical['scores']['grade']})")
    print(f"【美學-SongEval】 平均 {se_avg:.2f} / 5")
    for k, v in songeval.items():
        print(f" ・{SONGEVAL_LABELS.get(k, k)}:{v:.2f}")
    print("【美學-Audiobox】(1–10)")
    for k in ("PQ", "CE", "CU", "PC"):
        if k in audiobox:
            print(f" ・{AUDIOBOX_LABELS[k]}:{audiobox[k]:.2f}")
    print(f"【詞曲文本】 把歌詞貼給評審說「評詞」即可")
    print("-" * 54)
    print(f"完整報告:{out_path}")


if __name__ == "__main__":
    main()
