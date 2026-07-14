---
title: 歌曲評審團
emoji: 🎼
colorFrom: orange
colorTo: amber
sdk: gradio
sdk_version: 6.20.0
app_file: app.py
pinned: false
license: mit
short_description: 免費歌曲評審團（物理+美學+情感+AI詞評）
---

# 🎼 歌曲評審團

> 貼上 SUNO／YouTube 連結或上傳音檔，AI 幫你評歌：物理量測 ＋ 音樂家美學 ＋ 情感弧線 ＋ 詞評。
> 免費、雲端、零安裝。也支援 [Windows 本機執行](#windows-安裝)。

---

## 目錄

- [概覽](#概覽)
- [三關評審流程](#三關評審流程)
- [快速開始](#快速開始)
- [檔案說明](#檔案說明)
- [部署方式](#部署方式)
- [設定（維護者）](#設定維護者)
- [技術架構](#技術架構)
- [授權與第三方模型](#授權與第三方模型)

---

## 概覽

歌曲評審團是一套**全自動歌曲評審系統**，將音訊分析、美學模型、情緒計算與 LLM 詞評整合在一條管線中。

一首歌經過四道關卡：

```
輸入（SUNO 連結 / YouTube 連結 / 上傳音檔 + 歌詞）
  │
  ▼
① 物理量測 —— song_scorer.py（librosa + pyloudnorm + parselmouth）
  │  7 項指標：響度、動態範圍、頻譜平衡、立體聲寬度、削波檢測、層次鋪陳、和聲豐富度
  │
  ▼
② 美學模型 —— SongEval（五維 1-5）+ Audiobox（四軸 1-10）
  │
  ▼
   情感弧線 —— emotion_arc.py（NRC-VAD Russell 模型，Valence × Arousal）
  │
  ▼
③ AI 詞評 —— DeepSeek → Groq LLM 供應商鏈
    七維度雙分數 + 情感三支柱 + 合議庭裁決
  │
  ▼
   成績單 + 弧線圖 + 詞評報告 + 排行榜
```

每首歌約 **2–3 分鐘**完成（首次需載模型較久）。結果只限本人檢視，滿意後可選擇性放上公開排行榜。

---

## 三關評審流程

### ① 第一關：物理量測

以 `librosa`、`pyloudnorm`、`parselmouth` 對音檔進行 **7 項客觀量測**，各項 0–100 分後加權。

| 維度 | 權重 | 量測方法 |
|------|------|----------|
| 🎚 整體響度 | 15% | pyloudnorm integrated LUFS（以 -14 LUFS 串流標準為高分中心） |
| 📊 動態範圍 | 20% | 峰值／均方根比（crest factor），4–16 dB 最佳 |
| 🎛 頻譜平衡 | 20% | STFT 能量分四區（低/中低/中高/空氣），偏離合理範圍扣分 |
| 🔊 立體聲寬度 | 10% | (L−R)/(L+R) RMS 比 + 左右相關係數 |
| ✂ 削波檢測 | 10% | 樣本觸頂比例（>0.999） |
| 🏗 層次鋪陳 | 15% | 切 8 段量 RMS 落差與頻譜質心變異 |
| 🎵 和聲豐富度 | 10% | 諧波分離後 chroma CQT 音級數 + 和弦變化率 |

**前置偵測**：自動估算調性（Krumhansl-Kessler chroma correlation）與 BPM（全曲拍點網格外插）。

**選配**：提供人聲軌或啟用 demucs 自動分離時，額外評比音準、節奏、顫音、音域等 7 項演唱指標。

### ② 第二關：美學模型

| 模型 | 來源 | 維度 | 評分範圍 |
|------|------|------|----------|
| 🎓 **SongEval** | ASLP-lab（16 位音樂人 × 2,399 首標註訓練） | 連貫性、音樂性、記憶點、結構清晰度、人聲自然度 | 1–5 |
| 🏭 **Audiobox Aesthetics** | Meta | PQ 製作品質、CE 內容感染力、CU 實用性、PC 複雜度 | 1–10 |

CE < 7 時觸發「親聽檢查」警報。

### 中介：情感弧線

以 Russell (1980) 情感環狀模型為理論基礎，透過 NRC-VAD 心理學詞庫（加拿大國家研究院）對歌詞逐段量測 Valence（愉悅度）與 Arousal（激動度），產出 matplotlib 雙圖。

數據注入詞評 prompt 供合議庭裁決交叉引用。

### ③ 第三關：AI 詞評

**LLM 供應商鏈**（品質優先，自動 fallback）：

| 順位 | 供應商 | 模型 | 特性 |
|------|--------|------|------|
| 🥇 | DeepSeek | deepseek-v4-pro | 671B，中文母語最強，240s timeout |
| 🥈 | Groq | gpt-oss-120b | 免費、快，90s timeout |
| 🥉 | Groq | llama-3.3-70b | 免費、最穩底線，90s timeout |

沒設金鑰自動跳過該家。回應太短（< 300 字）換下一家。

**詞評包含**：題材定尺 → 七維度雙分數表格（作品分 + 爆款分）→ 情感三支柱 → 傳播假設檢查表 → 句級修法 → 場景適配表 → 合議庭裁決（交叉引用物理+美學數據）。

### 排行榜計分

```
總分 = 詞分×10×0.4 + (SongEval平均÷5×100)×0.3 + CE×10×0.3
```

- 詞 40% + SongEval（美學）30% + CE（感染力）30%
- 物理分獨立顯示為「發行就緒度」，不混入總分
- 最多 100 名，同名自動加 v2/v3 去重
- 前端只持 `audio_hash`，分數由伺服器重算，無法偽造

### 快取機制

三張快取表（私有 HF dataset），聰明重評省算力：

| 命中狀態 | 行為 | API 花費 |
|----------|------|----------|
| 🟢 完全命中（同音檔 × 同詞） | 整份沿用 | 零 |
| 🟡 詞評命中、母帶換了 | 沿用詞評 + 僅重生合議庭裁決 | 小額 |
| 🔴 全新 | 完整三關 + 詞評 | 全額 |

---

## 快速開始

### 本機執行（需 macOS/Linux 或 Windows + Python 3.10+）

**Windows 使用者**：執行 `setup_windows.bat` 一鍵安裝，然後 `run.bat` 啟動。

**macOS / Linux**：

```bash
# 克隆
git clone https://github.com/lunkerchen/song-jury.git
cd song-jury

# 安裝依賴
pip install -r requirements.txt
pip install gradio requests huggingface_hub

# 啟動 Gradio 介面
python app.py
```

瀏覽器打開 http://localhost:7860，貼上 SUNO/YouTube 連結或上傳音檔即可評分。

### 僅執行物理量測

```bash
python song_scorer.py 歌曲.mp3 --json report.json
```

### 完整三關（本機離線模式）

```bash
python jury.py https://suno.com/song/xxxx
```

自動下載 → 物理量測 → SongEval → Audiobox，輸出 `歌名_評審團.json`。

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| `app.py` | Gradio 主應用 — 三關排程、LLM 供應商鏈、快取讀寫、排行榜 API |
| `jury.py` | 下載整合器 — SUNO/YouTube/上傳檔 → 解析 + 調用三關子程序 |
| `song_scorer.py` | 物理量測核心 — 7 項指標（librosa/pyloudnorm/parselmouth） |
| `emotion_arc.py` | 情感弧線分析 — NRC-VAD Russell 模型，Valence × Arousal |
| `requirements.txt` | Python 依賴（torch 2.8 + muq + audiobox + librosa 等） |
| `ALGORITHM.md` | 完整演算法架構文件 |
| `architecture.html` | 視覺化架構圖（離線瀏覽） |
| `architecture_gzh.html` | 公眾號排版版演算法說明 |
| `README.md` | 本文件 |

---

## 部署方式

### 選項 A：Hugging Face Space（推薦）

1. 在 [hf.co](https://huggingface.co) 註冊帳號
2. 建立新 Space → SDK 選 Gradio → 硬體選 ZeroGPU（免費）
3. 將本 repo 檔案推送至該 Space
4. 在 Space Settings → Secrets 設定：
   - `DEEPSEEK_API_KEY`（選填）
   - `GROQ_API_KEY`（選填）
   - `HF_TOKEN`（選填，用於快取與排行榜）

### 選項 B：VPS / 自有伺服器

```bash
# 安裝系統依賴
sudo apt install ffmpeg

# 安裝 Python 依賴
pip install -r requirements.txt

# 啟動
python app.py --server.port=7860
```

### 選項 C：本機測試

無需對外公開時直接 `python app.py`，你的 M5 Max 48GB 完全可跑。

---

## 設定（維護者）

| 環境變數 | 必填 | 說明 |
|----------|------|------|
| `DEEPSEEK_API_KEY` | 否 | DeepSeek API 金鑰（詞評優先使用） |
| `GROQ_API_KEY` | 否 | Groq API 金鑰（免費，自動 fallback） |
| `HF_TOKEN` | 否 | Hugging Face 寫入權杖（快取與排行榜需要） |

未設定任何 LLM 金鑰時，詞評會退回「可複製的 prompt」讓使用者自行複製到 ChatGPT/Claude 評。

---

## 技術架構

```
song-jury/
├── app.py            ← Gradio 主控（三關排程 + LLM chain + 快取 + 排行榜 API）
├── jury.py           ← 下載整合器（SUNO/YT/上傳）
├── song_scorer.py    ← 物理量測核心（7 項指標）
├── emotion_arc.py    ← NRC-VAD 情緒分析
├── requirements.txt、README.md、ALGORITHM.md、architecture.html
└── .gitattributes、.gitignore
```

啟動時 runtime 取得的第三方資源：
- `SongEval/` — `git clone https://github.com/ASLP-lab/SongEval.git`（CC BY-NC-SA）
- `lexicon/` — NRC-VAD Lexicon（私有快取 → 官方學術站備援）

### 依賴棧

| 層 | 套件 |
|------|------|
| 物理量測 | librosa, numpy, scipy, soundfile, pyloudnorm, praat-parselmouth, matplotlib |
| 美學 A | torch, torchaudio, muq==0.1.0 |
| 美學 B | audiobox-aesthetics==0.0.4, transformers, einops |
| 詞評 | requests, huggingface_hub |
| 輸入 | yt-dlp |

---

## 授權與第三方模型

- 本專案程式碼：MIT © lunkerchen
- **SongEval**（CC BY-NC-SA，非商用）：不隨本 repo 散布，啟動時自 [ASLP-lab/SongEval](https://github.com/ASLP-lab/SongEval) 取得
- **NRC-VAD 情緒詞典**（研究用途免費，禁再散布）：不隨附，啟動時自官方學術站或私有快取取得
- **Meta Audiobox Aesthetics**：依 `requirements.txt` 安裝，使用其原始授權條款

### 原始版

基於 [vava22684/song-jury](https://huggingface.co/spaces/vava22684/song-jury)（貓貓滿屋）重製，改為橘系主題。

---

*詳細演算法說明見 [`ALGORITHM.md`](ALGORITHM.md) 與 [`architecture.html`](architecture.html)。*
