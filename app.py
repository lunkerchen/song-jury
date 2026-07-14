# -*- coding: utf-8 -*-
"""app.py — song-jury Hugging Face Space 版(免費 CPU + NVIDIA API 詞評)

與地端版差異:
1. 單一 Python 環境(HF 無雙 venv)→ 子程序用 sys.executable。
2. 強制 CPU(HF CPU Basic 無 GPU)。
3. 第三關詞評改串 NVIDIA 免費 API(build.nvidia.com);金鑰放 Space Secret「NVIDIA_API_KEY」,
伺服器端呼叫、不外露。沒金鑰時退回「可複製的 prompt」。
4. SongEval(CC BY-NC-SA)不隨 repo 散布 → 啟動時 runtime clone(只在伺服器端用,不對外提供下載)。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import gradio as gr
import requests

try:
    import spaces  # HF ZeroGPU 環境才有
except ImportError:  # 本機無 spaces → no-op 裝飾器(相容 @spaces.GPU 與 @spaces.GPU(...))
    class _SpacesStub:
        @staticmethod
        def GPU(fn=None, **kwargs):
            return (lambda f: f) if fn is None else fn
    spaces = _SpacesStub()


@spaces.GPU
def _zerogpu_probe():
    """ZeroGPU 要求啟動時至少偵測到一個 @spaces.GPU 函式才肯執行。
    本工具實際在 CPU 子程序評分,這探針只為通過啟動檢查——不會被呼叫、不佔 GPU 配額。"""
    return "ok"


BASE = Path(__file__).parent.resolve()
ENV = {**os.environ, "PYTHONUTF8": "1", "CUDA_VISIBLE_DEVICES": "-1"}  # HF 無 GPU,強制 CPU
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# 詞評供應商鏈(品質優先 → 免費後備,逐家往後退):
# ① DeepSeek v4-pro(671B 中文母語,實測神級但慢 ~78s、燒她額度)
# ② Groq gpt-oss-120b(免費、快 ~9s、會交叉引用數字)
# ③ Groq llama-3.3-70b(免費、必吃得下、最穩的底)
# 每家都走 OpenAI 相容 /chat/completions;哪家金鑰沒設(Space Secret)就自動跳過那家。
# 格式:(顯示名, 端點, 模型, 金鑰環境變數名, 逾時秒)
LLM_PROVIDERS = [
    ("DeepSeek v4-pro", "https://api.deepseek.com/chat/completions", "deepseek-v4-pro", "DEEPSEEK_API_KEY", 240),
    ("Groq gpt-oss-120b", GROQ_URL, "openai/gpt-oss-120b", "GROQ_API_KEY", 90),
    ("Groq llama-70b", GROQ_URL, "llama-3.3-70b-versatile", "GROQ_API_KEY", 90),
]


# ── 啟動時取得 SongEval(授權因素不 commit 進 repo,runtime clone 到容器內)──
def _ensure_songeval():
    if not (BASE / "SongEval" / "eval.py").exists():
        try:
            subprocess.run(["git", "clone", "--depth", "1",
                           "https://github.com/ASLP-lab/SongEval.git", str(BASE / "SongEval")],
                          check=True, timeout=600)
            print("SongEval 已取得")
        except Exception as e:
            print("SongEval clone 失敗(第二關 A 將不可用):", e)


_ensure_songeval()


# ── 啟動時取得 NRC-VAD 情緒詞典(情感弧線用;禁再散布→不 commit,啟動時自官方源抓進容器)──
def _ensure_nrcvad():
    """取得情感弧線用的 NRC-VAD 詞典。① 優先從私有 HF 資料集抓已處理好的小檔(zh/en_vad.tsv,
    合計 <1MB,HF 機房又快又穩、無外站依賴);② 抓不到才退回官方學術站抓 41MB 原檔(情感弧線.py 首跑自建)。
    禁再散布→私有快取屬個人用途、非公開散布。啟動先暖身、評分時再惰性確保(冷啟/重啟空窗自癒)。"""
    zh = BASE / "lexicon" / "zh_vad.tsv"
    en = BASE / "lexicon" / "en_vad.tsv"
    if zh.exists() and en.exists():
        return True
    # ① 優先:私有 HF 資料集(需 HF_TOKEN 有該 dataset 讀取權)
    tok = os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HUGGING_FACE_HUB_TOKEN", "").strip()
    if tok:
        try:
            import shutil
            from huggingface_hub import hf_hub_download
            (BASE / "lexicon").mkdir(parents=True, exist_ok=True)
            for fn, dest in (("zh_vad.tsv", zh), ("en_vad.tsv", en)):
                shutil.copy(hf_hub_download(repo_id="labangram/nrcvad-cache", repo_type="dataset",
                                            filename=fn, token=tok), dest)
            print("NRC-VAD 詞典已從私有快取取得")
            return True
        except Exception as e:
            print("私有快取取得失敗,退回官方站:", e)
    # ② 備援:官方學術站抓 41MB 原檔;重試 3 次
    marker = BASE / "lexicon" / "nrc-vad" / "NRC-VAD-Lexicon-Aug2018Release" / "NRC-VAD-Lexicon.txt"
    if marker.exists():
        return True
    import urllib.request, zipfile, io as _io
    url = "https://saifmohammad.com/WebDocs/VAD/NRC-VAD-Lexicon-Aug2018Release.zip"
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36", "Accept": "*/*"}
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=180).read()
            dest = BASE / "lexicon" / "nrc-vad"
            dest.mkdir(parents=True, exist_ok=True)
            zipfile.ZipFile(_io.BytesIO(data)).extractall(dest)
            print("NRC-VAD 已從官方站取得")
            return True
        except Exception as e:
            print(f"NRC-VAD 官方站取得失敗(第 {attempt + 1}/3 次):", e)
    return False


_ensure_nrcvad()  # 啟動暖身;失敗也沒關係,評分時 _ensure_nrcvad() 會再惰性重試


def _run(cmd):
    return subprocess.run(cmd, cwd=str(BASE), env=ENV, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


# ── 第三關詞評:LLM 多供應商鏈 ──
def _strip_report(raw):
    txt = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    txt = re.split(r"\n[\s\*\_#>-]\*親聽檢查", txt)[0]
    txt = re.split(r"\n[\s\*\_#-]\*評審體系[｜|]", txt)[0]
    txt = re.split(r"\n[\s\*\_#-]\*本報告為診斷性評審", txt)[0]
    txt = re.split(r"\n[\s\*\_#>\*-]\*【?詞總分", txt)[0]
    return txt.strip()


def _parse_lyric_score(raw):
    """取排行榜排名用的綜合詞分(0–10)。抓不到回 None。
    ① 先抓模型結尾的「【詞總分】X.X」(寬鬆:後 15 字內第一個數字,容忍 **／：／/10)。
    ② 後備:模型沒吐/吐歪那行時,從七維度表格的「作品分」欄取平均(每列第一個 0–10 數字),
    這樣只要有評出七維度就一定有詞分,排行榜不會因模型漏寫一行就漏收(LLM 隨機性防呆)。"""
    raw = raw or ""
    m = re.search(r"詞總分[^\d]{0,15}(\d+(?:\.\d+)?)", raw)
    if m:
        try:
            return max(0.0, min(10.0, float(m.group(1))))
        except Exception:
            pass
    scores = []
    for line in raw.splitlines():
        if line.count("|") >= 3:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            for c in cells[1:]:
                mm = re.fullmatch(r"\*\*\s*(\d+(?:\.\d+)?)\s*\*\*", c)
                if mm:
                    v = float(mm.group(1))
                    if 0 <= v <= 10:
                        scores.append(v)
                    break
    if scores:
        return round(sum(scores) / len(scores), 1)
    return None


def _llm_call(prompt, min_len=300, max_tokens=5000):
    """詞評 LLM 核心:DeepSeek→Groq 多供應商逐家往後退,回 (顯示文字, raw)。min_len 太短=被 thinking 吃掉→換下一家。"""
    last = None
    for name, url, model, key_env, tmo in LLM_PROVIDERS:
        key = os.environ.get(key_env, "").strip()
        if not key:
            continue
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4, "max_tokens": max_tokens}
        for _ in range(2):
            try:
                r = requests.post(url, timeout=tmo,
                                  headers={"Authorization": f"Bearer {key}"}, json=body)
                r.raise_for_status()
                msg = r.json()["choices"][0]["message"]
                raw = msg.get("content") or msg.get("reasoning_content") or ""
                txt = _strip_report(raw)
                if len(txt) >= min_len:
                    return txt, raw
                last = RuntimeError(f"{name} 回應過短({len(txt)} 字)")
                break
            except Exception as e:
                last = e
        if last:
            raise last
    return "", ""


def groq_judge(prompt):
    txt, raw = _llm_call(prompt, min_len=300)
    return txt, _parse_lyric_score(raw)


# ── 成績單整理 ──
def _score_table(merged):
    p = merged["layer1_physical"]
    se = merged["layer2_songeval_1to5"]
    ab = merged["layer2_audiobox_1to10"]
    meta = p.get("meta", {})
    q5 = lambda x: "優秀" if x >= 4.25 else "良好" if x >= 3.5 else "中等" if x >= 2.75 else "偏低" if x >= 2 else "很低"
    q10 = lambda x: "優秀" if x >= 8 else "良好" if x >= 6.5 else "中等" if x >= 5 else "偏低" if x >= 3 else "很低"
    rows = []
    name = Path(merged.get("file", "")).stem
    if name:
        rows.append(["__TITLE__", name])
    dur = meta.get("duration", 0) or 0
    summ = "・".join(x for x in [meta.get("key", ""),
                    (f'{round(meta["bpm"])} BPM' if meta.get("bpm") else ""),
                    (f'{int(dur // 60)}:{int(dur % 60):02d}' if dur else "")] if x)
    rows.append(["🎚 物理技術(總分/100)", f'{p["scores"]["total"]}({p["scores"]["grade"]})｜{summ}'])
    phys = {"loudness": "整體響度", "dynamic_range": "動態範圍", "spectral_balance": "頻譜平衡",
            "stereo": "立體聲寬度", "clipping": "削波檢測", "structure": "層次鋪陳", "harmony": "和聲豐富度"}
    for k, v in p.get("mix_detail", {}).items():
        rows.append([f"　・{phys.get(k, k)}", f'{v.get("score","")}｜{v.get("comment","")}'])
    se_lab = {"Coherence": "整體連貫性", "Musicality": "整體音樂性", "Memorability": "記憶點",
              "Clarity": "結構清晰度", "Naturalness": "人聲自然度"}
    se_desc = {"整體連貫性": "段落銜接、整體流暢度", "整體音樂性": "旋律與編曲的音樂質感",
               "記憶點": "hook／旋律讓人記住的程度", "結構清晰度": "主副歌結構是否清楚",
               "人聲自然度": "人聲聽感的自然程度"}
    if se:
        se_avg = sum(se.values()) / len(se)
        _lowk = min(se, key=se.get)
        _lowlab = se_lab.get(_lowk, _lowk)
        _tail = f"({_lowlab} {se[_lowk]:.2f} 低於 4)" if se[_lowk] < 4 else "(全維達 4+)"
        rows.append(["🎓 SongEval(音樂人模型)", f'平均 {se_avg:.2f} / 5｜五維{q5(se_avg)}{_tail}'])
    else:
        rows.append(["🎓 SongEval(音樂人模型)", "—"])
    for k, v in se.items():
        lab = se_lab.get(k, k)
        rows.append([f"　・{lab}", f"{v:.2f} / 5｜{q5(v)} · {se_desc.get(lab, '')}"])
    ab_lab = {"PQ": "製作品質", "CE": "內容感染力", "CU": "內容實用性", "PC": "製作複雜度"}
    ab_desc = {"PQ": "混音/清晰度的技術水準", "CE": "情緒感染與聆聽愉悅度",
               "CU": "場景適配的廣度", "PC": "編曲密度(描述性,非越高越好)"}
    ab_sum = f'製作品質 {ab.get("PQ",0):.2f} · 感染力 {ab.get("CE",0):.2f}' if ab else ""
    rows.append(["🏭 Audiobox(Meta)", f'{ab_sum}｜Meta 美學模型四軸'])
    for k in ("PQ", "CE", "CU", "PC"):
        if k in ab:
            warn = " ⚠️ CE<7 觸發親聽情緒複核" if (k == "CE" and ab[k] < 7) else ""
            rows.append([f"　・{ab_lab[k]} {k}", f"{ab[k]:.2f} / 10｜{q10(ab[k])} · {ab_desc[k]}{warn}"])
    return rows


def _measure_block(merged):
    """把已算出的物理/美學/情感數字整理成一段,餵給詞評模型做跨關交叉引用。"""
    if not merged:
        return ""
    try:
        p = merged["layer1_physical"]
        se = merged.get("layer2_songeval_1to5", {})
        ab = merged.get("layer2_audiobox_1to10", {})
        st = p.get("mix_detail", {}).get("structure", {})
        lines = [f'- 物理總分 {p["scores"]["total"]}（{p["scores"]["grade"]} 級）']
        if st:
            lines.append(f'- 物理·層次鋪陳分 {st.get("score","?")}｜{st.get("comment","")}')
        if se:
            se_avg = sum(se.values()) / len(se)
            _lowk = min(se, key=se.get)
            _map = {"Coherence": "整體連貫性", "Musicality": "整體音樂性", "Memorability": "記憶點",
                    "Clarity": "結構清晰度", "Naturalness": "人聲自然度"}
            lines.append(f'- SongEval 五維平均 {se_avg:.2f}／5,最低維 {_map.get(_lowk,_lowk)} {se[_lowk]:.2f}')
        if ab:
            lines.append(f'- Audiobox 感染力 CE {ab.get("CE","?")}、製作品質 PQ {ab.get("PQ","?")}（滿分 10；CE<7 代表情緒感染偏低）')
        return ("\n===== 本曲量測數據(⚠️ 請在『合議庭裁決』與相關維度【交叉引用】這些數字佐證,"
                "不要自己重算,例如「SongEval 4.03 這批最低、印證中等」「物理層次分低、印證無 build」)=====\n"
                + "\n".join(lines) + "\n")
    except Exception:
        return ""


LEAN_RUBRIC = """《評詞標準·精要》(單一模式,只評「詞」)

【題材定尺】先判題材賽道,決定尺:嘻哈=看多押/內韻/flow,punchline 宣言不扣;民謠=白描敘事不算說教;搖滾/Emo=態度宣言是類型慣例不扣;古風/國風=文雅凝練,口語白話反而扣分,韻要工整;流行=標準判定。

【七維度打分 0–10,每個分數必須【引原句】佐證,否則評審無效;每維度都要給「作品分」與「爆款分」兩個分數】
1 主題立意(作品 20%／爆款 15%):一個中心概念統攝全篇(歌眼),新鮮、有反轉。
2 敘事結構(20%／15%):起承轉合、副歌功能、Final Chorus 變奏、首尾呼應;爆款看 hook 多前面。
3 意象畫面(15%／5%):show-don't-tell;物象句不自動加分,陳腔意象(雨/街燈/心碎堆疊)照扣;直抒句只在高潮/宣言位合法。
4 音韻押韻(10%／5%):韻腳統一度、換韻意圖、rap 段 flow。
5 歌唱性(15%／20%):只列風險點、不給小數分(音節密度、開閉口音、聲調咬合)。
6 聲腔與新鮮度(10%／10%):語氣人格一致、語言新鮮度、cliché 污染檢查。
7 金句記憶點(10%／30%):能不能被抄走、剪成 15 秒字幕。

【情感三支柱(只准交證據,禁止「我覺得很感人」)】
①客觀對應物:逐句分類「物象句(演出來)vs 說教句(說出來)」,算比例、引原句;telling 多於 showing 要扣,並點名這是不是拉低作品分的主因。
②張力管理:期待建立→延宕→解決是否存在;平坦只在「意圖移動卻沒動成」時扣分。
③情感弧線:看段落間相對情緒移動(反諷歸你判讀)。

【傳播假設檢查表(verdict 只用 ✅／❌／(部分))】
hook 或核心句 30 秒內出現? / 有可直接當短影音字幕的句子? / 可模仿與二創場景 3 種以上? / 受眾語境明確(寫給誰、在什麼情境聽)?

【評分錨點】5=功能性(AI 罐頭詞)｜7=工整有亮點｜8=專業可發行｜9=傑出(金句+弧線+概念統一)｜10=投獎級,不輕易給。LLM 評審偏寬(分數集中 7–9),讀差距別讀絕對值。"""


def _lyric_prompt(lyrics, merged=None):
    return ("你是專業歌曲評審。這是一次性自動評分(無法來回對話),請【直接開始評】,"
            "不要詢問或確認、不要說『收到請求』之類開場白,一律採「單一模式」。"
            "請【嚴格依照】下方《評詞標準·精要》評這首歌的「詞」,並【完整輸出下面每一段,寫深、有分析、不要精簡,不要只寫一句帶過】:\n"
            "1. **七維度雙分數表格**(Markdown 表格,欄位:維度｜作品分｜爆款分｜引原句評語):逐一列出全部七個維度,每維度都要給作品分與爆款分兩個分數,並【引原句】舉證。\n"
            "2. **情感三支柱**:①客觀對應物(數物象句vs說教句、引句分析、指出是否為拉低作品分主因)②張力管理(鋪陳→延宕→解決的完整分析)③情感弧線判讀——每支柱都要具體、有分析。\n"
            "3. **傳播假設檢查表**:hook 30秒內、可剪句、二創場景、受眾語境,逐項判定並說明依據。\n"
            "4. **句級修法**:逐句挑問題句,每條給【問題→修改建議→示範改寫】,挑 3 條以上。\n"
            "5. **場景適配**(表格:場景｜適配強弱｜原因):作品發行(串流/YT)、短影音爆點、舞曲現場、品牌配樂。\n"
            "6. 合議庭裁決:【務必獨立一行,以 `## 合議庭裁決` 開頭】,總結最強項與硬傷,【務必交叉引用下方本曲量測數據】(SongEval 平均/最低維、Audiobox CE、物理層次分)佐證你對「藝術完成度」與「是否無 build」的判斷,給明確發行建議與必修三步——這是報告的靈魂,要有立場、有份量。\n"
            '全部寫完後,最後【另起一行】只輸出一行綜合詞分供系統排名用,格式嚴格為「【詞總分】X.X」(X.X 為 0–10 一位小數,綜合這首『詞』整體水準、對照評分錨點)。⚠️這行務必輸出、絕不可省略;X.X 只寫純數字,不要加粗體(不要 **)、不要寫「/10」、不要加任何其他文字或符號。\n'
            "只評『詞』;物理技術/美學/親聽檢查由儀器與人耳負責,你不要寫那些,也不要在結尾附評審體系或免責聲明。"
            "全程用繁體中文(即使歌詞是簡體,評語也用繁體),直接輸出完整評審報告。\n\n"
            f"===== 評詞標準·精要 =====\n{LEAN_RUBRIC}\n\n"
            f"===== 待評歌詞 =====\n{lyrics}\n"
            f"{_measure_block(merged)}\n"
            "現在請直接輸出完整六段評審報告。")


def _jpath_from_stdout(stdout):
    for line in reversed(stdout.splitlines()):
        m = re.match(r"\s*完整報告[：:]\s*(.+)", line)
        if m:
            return Path(m.group(1).strip())
    return None


# ── 儲存:公開排行榜(網頁讀)+ 私有快取(整份報告+詞評,含歌詞句故不公開)──
LEADERBOARD_REPO = "labangram/song-jury-leaderboard"
CACHE_REPO = "labangram/song-jury-cache"
RUBRIC_VERSION = "v8"
ENGINE_VERSION = "e1"
CACHE_MAX = 500


def _tok():
    return os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HUGGING_FACE_HUB_TOKEN", "").strip()


def _norm_link(link):
    return (link or "").strip().split("?")[0].split("#")[0].rstrip("/").lower()


def _lyrics_hash(lyrics):
    norm = re.sub(r"\s+", "", lyrics or "")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16] if norm else ""


def _split_verdict(text):
    lines = (text or "").split("\n")
    for i, ln in enumerate(lines):
        s = ln.strip()
        if "合議庭裁決" in ln and (s.startswith(("#", ">", "*", "-")) or "**" in ln
                                     or re.match(r"\s*[六6][\.、,]", ln) or len(s) <= 20):
            return "\n".join(lines[:i]).rstrip(), "\n".join(lines[i:]).strip()
    return (text or "").rstrip(), ""


def _regen_verdict(body, lyrics, merged):
    prompt = ("你是專業歌曲評審。以下是先前已完成的『詞評主體』(七維度/情感/傳播/句級/場景 已寫好),"
              "以及【本次母帶重新量測】的音訊數據。歌詞沒變、詞評主體照舊,"
              '請【只輸出一段以「## 合議庭裁決」開頭的裁決】:總結最強項與硬傷,'
              "【務必交叉引用下方新的量測數據】(SongEval 平均/最低維、Audiobox CE、物理層次分)佐證藝術完成度與是否無 build,"
              "給明確發行建議與必修三步。只輸出這一段,不要重印前面的表格或其他段落,全程繁體中文。\n\n"
              f"===== 詞評主體(已完成,勿重寫)=====\n{(body or '')[:4500]}\n\n"
              f"===== 待評歌詞 =====\n{lyrics}\n"
              f"{_measure_block(merged)}\n"
              "現在只輸出『## 合議庭裁決』這一段。")
    try:
        txt, _ = _llm_call(prompt, min_len=120, max_tokens=1600)
        if txt:
            return txt if "合議庭裁決" in txt else "## 合議庭裁決\n" + txt
    except Exception as e:
        print("裁決重生失敗,沿用舊裁決:", e)
    return ""


def _cache_load_all():
    out = {"report": {}, "lyric": {}, "source": {}}
    tok = _tok()
    if not tok:
        return out
    from huggingface_hub import hf_hub_download
    for key, fn in (("report", "reports.json"), ("lyric", "lyrics.json"), ("source", "sources.json")):
        try:
            fp = hf_hub_download(repo_id=CACHE_REPO, repo_type="dataset", filename=fn,
                                 force_download=True, token=tok)
            out[key] = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            pass
    return out


def _trim(d):
    if len(d) > CACHE_MAX:
        for k in list(d.keys())[:len(d) - CACHE_MAX]:
            d.pop(k, None)
    return d


def _cache_save(report, lyric, source):
    tok = _tok()
    if not tok:
        return
    import io as _io
    from huggingface_hub import HfApi, CommitOperationAdd
    try:
        HfApi(token=tok).create_commit(repo_id=CACHE_REPO, repo_type="dataset", operations=[
            CommitOperationAdd("reports.json", _io.BytesIO(json.dumps(_trim(report), ensure_ascii=False).encode("utf-8"))),
            CommitOperationAdd("lyrics.json", _io.BytesIO(json.dumps(_trim(lyric), ensure_ascii=False).encode("utf-8"))),
            CommitOperationAdd("sources.json", _io.BytesIO(json.dumps(_trim(source), ensure_ascii=False).encode("utf-8"))),
        ], commit_message="update caches")
    except Exception as e:
        print("快取寫入失敗(不影響評分結果):", e)


def _leaderboard_commit(entry=None, count_inc=0):
    tok = _tok()
    if not tok:
        print("無 HF_TOKEN → 排行榜略過")
        return False
    import io as _io
    from huggingface_hub import HfApi, hf_hub_download, CommitOperationAdd
    api = HfApi(token=tok)
    for _ in range(4):
        try:
            fp = hf_hub_download(repo_id=LEADERBOARD_REPO, repo_type="dataset",
                                 filename="leaderboard.json", force_download=True, token=tok)
            data = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            data = {"total_count": 0, "entries": []}
        data["total_count"] = int(data.get("total_count", 0)) + int(count_inc)
        if entry:
            ents = data.setdefault("entries", [])
            same = next((e for e in ents if entry.get("hash") and e.get("hash") == entry["hash"]), None)
            if same:
                same.update(entry)
            else:
                names = {e.get("name") for e in ents}
                if entry.get("name") in names:
                    n = 2
                    while f'{entry["name"]} v{n}' in names:
                        n += 1
                    entry["name"] = f'{entry["name"]} v{n}'
                ents.append(entry)
            data["entries"] = sorted(ents, key=lambda e: e.get("total", 0), reverse=True)[:100]
        try:
            api.create_commit(repo_id=LEADERBOARD_REPO, repo_type="dataset",
                              operations=[CommitOperationAdd("leaderboard.json",
                                                             _io.BytesIO(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")))],
                              commit_message="update leaderboard")
            return True
        except Exception as e:
            print("排行榜寫入衝突/失敗,重試:", e)
    return False


def _entry_from(merged, audio_hash, lyric_score):
    try:
        name = Path(merged.get("file", "")).stem
        se = merged.get("layer2_songeval_1to5", {})
        ab = merged.get("layer2_audiobox_1to10", {})
        se_avg = (sum(se.values()) / len(se)) if se else None
        ce = ab.get("CE")
        pscore = merged.get("layer1_physical", {}).get("scores", {})
        if not (name and (lyric_score is not None) and se_avg and (ce is not None)):
            return None
        total = round(lyric_score * 10 * 0.4 + se_avg / 5 * 100 * 0.3 + ce * 10 * 0.3, 1)
        return {"name": name, "total": total, "hash": audio_hash, "physical": pscore.get("total"),
                "aesthetic": round(se_avg, 2), "ce": round(ce, 2), "lyric": round(lyric_score, 1)}
    except Exception:
        return None


# ── 主流程 ──
def evaluate(link, audio_file, lyrics, progress=gr.Progress()):
    link_s = (link or "").strip()
    src = link_s or (audio_file or None)
    if not src:
        return [], None, "", "⚠️ 請給 SUNO/YouTube 連結,或上傳音檔。 🐾", ""

    cache = _cache_load_all()
    report, lyric_c, source_c = cache["report"], cache["lyric"], cache["source"]

    # ── 1. 決定音檔指紋 + 盡量短路音訊三關 ──
    audio_hash = ""
    if audio_file and not link_s:
        try:
            audio_hash = hashlib.sha256(Path(audio_file).read_bytes()).hexdigest()[:16]
        except Exception:
            audio_hash = ""
    elif link_s:
        audio_hash = source_c.get(_norm_link(link_s), "")

    merged = None
    rep = report.get(audio_hash) if audio_hash else None
    if rep and rep.get("ev") == ENGINE_VERSION and rep.get("merged"):
        merged = rep["merged"]
        progress(0.35, desc="認出這首評過,直接沿用音訊成績(不重跑三關)… 🐾")

    if merged is None:
        progress(0.1, desc="物理 + SongEval + Audiobox 評分中(CPU,約 2–3 分鐘;第一次更久要載模型)… 🐾")
        r = _run([sys.executable, str(BASE / "jury.py"), str(src)])
        if r.returncode != 0:
            return [], None, "", f"❌ 音訊評分失敗:\n```\n{(r.stderr or r.stdout)[-1000:]}\n```", ""
        jpath = _jpath_from_stdout(r.stdout)
        if not jpath or not jpath.exists():
            return [], None, "", f"❌ 找不到結果 JSON。\n```\n{r.stdout[-600:]}\n```", ""
        merged = json.loads(jpath.read_text(encoding="utf-8"))
        audio_hash = merged.get("audio_hash", "") or audio_hash
        rep = report.get(audio_hash)

    table = _score_table(merged)

    # ── 2. 歌詞 + 情感弧線 ──
    eff_lyrics = (lyrics or "").strip() or merged.get("fetched_lyrics", "").strip()
    has_lyrics = bool(eff_lyrics)
    lyr_hash = _lyrics_hash(eff_lyrics)

    arc_img = None
    if has_lyrics:
        progress(0.55, desc="情感弧線分析中… 🎭")
        _ensure_nrcvad()
        tmp = Path(tempfile.mkdtemp()) / "歌詞.txt"
        tmp.write_text(eff_lyrics, encoding="utf-8")
        _run([sys.executable, str(BASE / "emotion_arc.py"), str(tmp)])
        cand = tmp.with_name(tmp.stem + "_情感弧線.png")
        arc_img = str(cand) if cand.exists() else None

    # ── 3. 詞評 ──
    lyric_eval = ""
    lyric_score = None
    verdict_for_this = ""
    if has_lyrics:
        lc = lyric_c.get(lyr_hash)
        body = lc.get("body") if (lc and lc.get("rv") == RUBRIC_VERSION) else None
        rep_verdicts = rep.get("verdicts", {}) if rep else {}
        cached_verdict = rep_verdicts.get(lyr_hash, "")
        if body is not None and lyr_hash in rep_verdicts:
            lyric_eval = (body + "\n\n" + cached_verdict).strip()
            lyric_score = lc.get("score")
            verdict_for_this = cached_verdict
            note = "✅ 完成!(這首之前評過,整份沿用,零重算、不花額度) 🐾"
        elif body:
            progress(0.75, desc="詞沒變、母帶換了:沿用詞評、只用新音訊數字重寫合議庭裁決… 🎯")
            lyric_score = lc.get("score")
            verdict = _regen_verdict(body, eff_lyrics, merged)
            if verdict:
                lyric_eval = body + "\n\n" + verdict
                verdict_for_this = verdict
                note = "✅ 完成!(換了母帶:音訊重新評分,詞評沿用+合議庭裁決依新音訊數字更新) 🎯"
            else:
                old_v = next(iter(rep.get("verdicts", {}).values()), "") if rep else ""
                lyric_eval = (body + "\n\n" + old_v +
                              "\n\n> ⚠️ 註:上方合議庭裁決引用的是先前母帶的音訊數字,本次音訊數字以上方成績單為準。")
                verdict_for_this = old_v
                note = "✅ 完成!(換了母帶:音訊已重評;裁決重生失敗,沿用舊裁決並加註) 🎯"
        else:
            progress(0.75, desc="第三關詞評中(DeepSeek v4-pro,約 1 分鐘;失敗自動退免費 Groq)… 🎯")
            note_extra = ""
            full = ""
            try:
                full, lyric_score = groq_judge(_lyric_prompt(eff_lyrics, merged))
            except Exception as e:
                note_extra = f"(詞評 API 失敗:{e})"
            if full:
                body_new, verdict_new = _split_verdict(full)
                lyric_eval = full
                verdict_for_this = verdict_new
                lyric_c[lyr_hash] = {"body": body_new, "score": lyric_score, "rv": RUBRIC_VERSION}
                note = "✅ 完成!物理 + 美學 + 情感弧線 + 詞評,全都在。 🎯"
            else:
                note = f"✅ 音訊+情感完成,但第三關詞評沒產出{note_extra}。"
    else:
        note = "✅ 音訊完成,但沒拿到歌詞→跳過情感弧線與詞評。(SUNO 連結會自動抓詞;YouTube/上傳檔請在歌詞欄貼上。) 🐾"

    # ── 4. 私有快取 + 人次計數 ──
    progress(0.95, desc="收尾… 🐾")
    try:
        if audio_hash:
            base_rep = report.get(audio_hash, {})
            verdicts = dict(base_rep.get("verdicts", {}))
            if lyr_hash and lyric_eval:
                verdicts[lyr_hash] = verdict_for_this
            report[audio_hash] = {
                "merged": merged, "ev": ENGINE_VERSION,
                "source": _norm_link(link_s) if link_s else base_rep.get("source", ""),
                "lyr_hash": lyr_hash or base_rep.get("lyr_hash", ""),
                "lyric_score": lyric_score if lyric_score is not None else base_rep.get("lyric_score"),
                "verdicts": verdicts,
            }
            if link_s:
                source_c[_norm_link(link_s)] = audio_hash
            _cache_save(report, lyric_c, source_c)
    except Exception as e:
        print("快取更新略過:", e)
    try:
        _leaderboard_commit(entry=None, count_inc=1)
    except Exception as e:
        print("人次更新略過:", e)

    pub = audio_hash if _entry_from(merged, audio_hash, lyric_score) else ""
    return table, arc_img, lyric_eval, note, pub


def publish_song(audio_hash):
    audio_hash = (audio_hash or "").strip()
    if not audio_hash:
        return "⚠️ 請先完成一次評分,再放上排行榜。 🐾"
    rep = _cache_load_all()["report"].get(audio_hash)
    if not rep or not rep.get("merged"):
        return "⚠️ 找不到這首的評分紀錄(可能太久被清掉了),請重新評分一次再上榜。 🐾"
    entry = _entry_from(rep["merged"], audio_hash, rep.get("lyric_score"))
    if not entry:
        return "⚠️ 這首沒有完整的詞評分數,無法上榜(需要有歌詞跑出詞評)。 🐾"
    ok = _leaderboard_commit(entry=entry, count_inc=0)
    return f"🎉 已放上排行榜!目前總分 {entry['total']}。" if ok else "⚠️ 上榜失敗,請稍後再試一次。 🐾"


# ── UI(歌曲評審團 橘系主題)──
THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.orange,
    secondary_hue=gr.themes.colors.amber,
).set(
    body_background_fill="linear-gradient(150deg,#fef3c7,#fed7aa,#fdba74)",
    body_background_fill_dark="linear-gradient(150deg,#fef3c7,#fed7aa,#fdba74)",
    block_background_fill="rgba(255,255,255,0.90)",
    block_background_fill_dark="rgba(255,255,255,0.90)",
    block_radius="18px",
    block_shadow="0 8px 28px rgba(234,88,12,0.15)",
    button_primary_background_fill="#ea580c",
    button_primary_background_fill_hover="#c2410c",
    button_primary_text_color="#ffffff",
    input_background_fill="#ffffff",
    body_text_color="#431407",
    body_text_color_dark="#431407",
)

CAT_CSS = """
.gradio-container{max-width:920px!important;margin:0 auto!important;}
footer{display:none!important;}
h1,h2,h3{color:#9a3412!important;}
"""

with gr.Blocks(title="歌曲評審團", css=CAT_CSS, theme=THEME) as demo:
    gr.Markdown(
        "# 🎼 歌曲評審團\n"
        "第一關 物理量測 + 第二關 音樂家美學模型(SongEval/Audiobox)+ 情感弧線 + 第三關 AI 詞評。\n"
        "**免費、雲端、零安裝。** 一首約 2–3 分鐘,請耐心等。\n\n"
        "> 貼 **SUNO 連結** 會自動抓歌+抓詞(歌詞欄可留空);**YouTube 連結/上傳檔** 抓不到詞,"
        "請自己在歌詞欄貼上,第三關詞評才會跑。"
    )
    with gr.Row():
        with gr.Column():
            link = gr.Textbox(label="SUNO / YouTube 連結", placeholder="https://suno.com/song/… 或 youtube.com/…")
            audio = gr.Audio(label="或上傳音檔", type="filepath")
            lyrics = gr.Textbox(label="歌詞(SUNO 連結會自動抓,可留空;YT/上傳檔請貼)", lines=8,
                                placeholder="段落可用【】標記")
            btn = gr.Button("開始評分", variant="primary")
        with gr.Column():
            note = gr.Markdown()
            table = gr.Dataframe(headers=["項目", "分數 / 說明"], label="成績單(第一關物理 + 第二關美學)", wrap=True)
            arc = gr.Image(label="情感弧線圖(段落情緒移動)", type="filepath")
            lyric_eval = gr.Markdown(value="*（第三關 AI 詞評:評分完成後,完整詞評會顯示在這裡）*")
            pub_hash = gr.Textbox(visible=False)
            pub_btn = gr.Button("⭐ 把這首放上排行榜(看到分數滿意再按)", visible=False)
            pub_note = gr.Markdown()
    btn.click(evaluate, [link, audio, lyrics], [table, arc, lyric_eval, note, pub_hash],
              api_name="evaluate").then(
        lambda h: (gr.update(visible=bool(h)), ""), pub_hash, [pub_btn, pub_note])
    pub_btn.click(publish_song, pub_hash, pub_note, api_name="publish")


if __name__ == "__main__":
    demo.launch(ssr_mode=False, theme=THEME, css=CAT_CSS)
