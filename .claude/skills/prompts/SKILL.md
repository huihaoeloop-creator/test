---
name: prompts
description: 节点 04 · 把风格要素表变成各生图工具的 prompt，一款正反两条。当用户要求"出图指令""写 prompt""生图关键词""给 Midjourney 的指令"时使用。产出 prompts.json + midjourney.txt + ideogram.txt，供节点 05 批量生成。
---

# 出图指令

```
python -X utf8 scripts/prompts.py --style 03-style/style.json \
    --brief brief.json --plan 01-search/plan.json -o 04-prompts/
```

**`--plan` 别漏**。没有它，同一个方向的四款只有配色轮换的差别，生出来是四张
近似重复的图。脚本会提示，但提示不如别漏。

## 一款两条：正面 + 背面

交付 PPT 一页放正反两张，所以 prompt 从这里就是成对生成的，不是生完正面再
想办法补背面。

**正反一致性是这条链上最难的一环。** prompt 里写了 `same garment and colourway`，
但生图模型不保证两次生成是同一件衣服。实际做法：
- Midjourney 用 `--sref`（风格参考）或先出正面再用图生图做背面
- 选片时**成对看**，配不上的整对废掉重生，不要凑合用不同款的正反面

脚本给的是起点，不是保证。

## 四款怎么区分

两个轴，都来自已有数据，不是编的：

| 轴 | 来源 |
|---|---|
| 突出特征 | 方向的检索词轮换（merino → fine gauge → tonal → crew neck） |
| 主色 | 调色板轮换（第 N 款用第 N 个颜色打头） |

想要比这更大的差异，那是**设计决定**——去 style.json 里拆成不同方向，或者
补更多风格要素，不是在这里想办法。

## 工具

| 工具 | 角色 | 能不能自动 |
|---|---|---|
| **Ideogram** | 主力出款图 | 官方 API，`IDEOGRAM_API_KEY` |
| **Adobe Firefly** | 交付兜底 | 官方 API。唯一给 IP 赔偿的一家 |
| **PatternedAI** | 面料花型 | 人工。垂直做无缝循环，没有替代品 |
| **Midjourney** | 概念发散 | **不能。没有官方 API。** 第三方中转和 Discord self-bot 都违反 ToS |
| **即梦 AI** | 中文提示词 | 人工。API 走火山方舟，接入成本另算 |

选型理由和其他工具为什么没进主力，见 `docs/生图工具选型.md`。

**API key 只放环境变量。** 不写进文件、不提交进仓库、不发到聊天里。

要加工具就往 `TOOLS` 表里加一条，注明 mode 和授权边界——**加之前先确认它的
商用授权**。明确不用的记在 `NOT_USED` 里（比如 Leonardo 免费档保留对生成图的
分发权利，不能用于客户交付），免得下次又有人提。

## MJ 参数为什么是那几个

```
--ar 3:4 --style raw --s 250 --no text, watermark, logo, collage, multiple views
```

- `--style raw` 是关键：不加的话 MJ 会往「好看」的方向美化，出来的是海报不是
  可开发的款
- `--no text…` 挡掉它老爱加的字、logo 和拼图
- `--v` 默认不加，用账号自己的默认版本——**不替账号猜版本号**

## 风格要素没填全就别跑

```
⚠ 风格要素还差 3 项没填：领型、袖型、长度
   这些 prompt 缺了这几项，生出来的款不受控 —— 回节点 03 补
```

脚本此时以退出码 1 结束。**这是设计如此**：批量生成一轮的时间和额度成本最高，
在这里省下的五分钟，后面要用两小时赔。
