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
short_description: 免費歌曲評審團(物理+美學+情感+AI詞評)
---
# 🎼 歌曲評審團

免費、零安裝、雲端。貼 SUNO/YouTube 連結或上傳音檔，一次跑完三關：

1. **第一關 物理**：響度/動態/頻譜/立體聲/削波/層次/和聲 (librosa 量測)
2. **第二關 美學**：SongEval (16 位音樂人訓練，五維) + Meta Audiobox (四軸)
3. **第三關 詞評**：七維度雙分數（作品分/爆款分）+ 情感三支柱——由 DeepSeek→Groq LLM 產出

> ⏱ 本 Space 跑在 **免費 ZeroGPU/CPU**，一首約 **2–3 分鐘**（閒置後第一位訪客要載模型會更久）；要秒出可自行改用 GPU。

## 設定（維護者）

- **`DEEPSEEK_API_KEY`** (Space Secret，選填)：DeepSeek API 金鑰。沒設時自動退到 Groq 免費 API。
- **`GROQ_API_KEY`** (Space Secret，選填)：Groq API 金鑰（免費）。兩者都沒設時詞評改給可複製的 prompt。
- **`HF_TOKEN`** (Space Secret，選填)：寫入排行榜與快取需要。沒設時不影響評分，但無法上榜/快取。

## 授權與第三方模型

- 本專案程式：MIT © lunkerchen。
- **SongEval** (CC BY-NC-SA)：不隨本 repo 散布，Space 啟動時自官方源 runtime 取得（僅伺服器端使用，不對外提供下載）。
- **NRC-VAD 情緒詞典**（禁再散布，情感弧線用）：不隨附；未提供時情感弧線圖略過，不影響其他關。
- **Meta Audiobox Aesthetics**：依 `requirements.txt` 安裝。

## 原始版

基於 [vava22684/song-jury](https://huggingface.co/spaces/vava22684/song-jury)（貓貓滿屋）重製，改為橘系主題。
