---
name: collect
description: 节点 02 · 按检索方案收图，并给每一张登记出处台账。当用户要求"采集素材""收图""导入图片""建台账""看采集进度"时使用。产出 ledger.json + 分方向的素材目录，供节点 03 起以及节点 07 的 Sources 页读取。
---

# 素材采集与出处台账

```
python -X utf8 scripts/collect.py --plan plan.json --status
```

**这个节点真正的产出是台账，不是图。** 图丢了能重搜；出处丢了，这张图就废了——
交付 PPT 最后那页 Sources 靠它生成，哪张能进客户交付页、哪张只能内部看，
也全看台账里的 `channel` / `use` 字段。

## 开工前的闸

`plan.json` 里 `approved` 不是 `true`，脚本直接退出。这是节点 01 的人工确认闸，
**不要绕过去**，也不要在脚本里自动置 true。

## 两条收图路径

### 人工导出的目录（WGSN、手动存的品牌图）

```
python -X utf8 scripts/collect.py --plan plan.json --ingest 导出/ \
    --channel wgsn --direction 1 --source "WGSN AW27 Knitwear Key Items"
```

`--direction` 收序号（1 起）或方向名的一段。**必须给**——图从进来的那一刻就分好组，
后面节点 03 才不用重新归类。

按内容哈希（sha256）去重，跨渠道也认：同一张图从品牌官网和 Pinterest 各来一次，
只留一份。

### 客户官网 / 对标品牌官网（自动）

两个渠道分开：**客户官网**是客户自己在售的款，用来摸他们的版型习惯、克重、价位，
是判断新方向能不能落地的基准；**对标品牌官网**是竞品，用来看市场。两者都只能
内部看，但角色不同，分析时不能混。

```
node scripts/brand_collect.mjs <列表页URL> --probe          # 先看这个站什么结构
node scripts/brand_collect.mjs <列表页URL> --out 导出/seed --limit 30 \
    --match-plan plan.json                                  # 按方向分好
python -X utf8 scripts/collect.py --plan plan.json --ingest-tree 导出/seed \
    --channel client --source "客户官网"
```

`--match-plan` 拿商品名去撞各方向的检索词，命中多的那个赢，分方向落到子目录，
`--ingest-tree` 一次收整棵树。

**撞的是文字，不是图。** 商品名里没线索的（「Knit 011」「新品」）进 `unmatched`，
台账里方向留空并点名，`--status` 会单列一行「待人工归类」——不能默默塞进某个
方向，那等于污染数据。

`--probe` 把几条常见的商品卡片选择器各命中多少个报出来，**让人挑**，不是脚本
猜一个然后静悄悄抓错东西。抓完自动写 `sources.csv`，逐张带商品链接。

**先看 robots.txt，Disallow 就不抓，没有绕过开关。** 需要抓就先去拿书面许可。

抓不到东西时看「跳过」那行——`卡片里没图` / `下载失败` / `小于 N 字节`
三种原因分开报。图普遍偏小的站把 `--min-bytes` 调低。

默认每张图之间停 1.5 秒（`--delay`）。别为了快调到 0，把人家站点打疼了
是要担责任的。

### Pinterest 官方 API

```
set PINTEREST_ACCESS_TOKEN=...
python -X utf8 scripts/collect.py --plan plan.json --pinterest-board 1234567890 --direction 2
```

**只能拉本账号有权限的画板**——官方 API 没有开放全站搜索。所以 Pinterest 那份
配额的真实工作流是：**人先在画板里选片，脚本再把画板整个拉下来**。这既是合规
要求，也是 API 的客观限制。

### 淘宝 / 小红书（人工）

两个都**不能自动抓**——ToS 禁止，且反爬很硬。跑节点 01 时加 `--picklists`
会自动出选品清单，人工照着在浏览器里选，导出后按上面「人工导出的目录」那条收。

清单里的检索词是**中文**：英文词在这两个平台搜不出东西，脚本会把方向的英文词
反查回中文（`cable knit` → 绞花 / 麻花，同义词都给，出来的货不一样）。反查不到的
单独列出来请人自己判断，**不会把英文词硬塞进去让人白试一轮**。

两个平台的图性质不同：
- **淘宝**是卖家的商品图，看市场和供应链——什么货型现成、什么价位、什么好卖
- **小红书**是消费者的 UGC，看需求端——什么内容有互动，评论区在问什么

**小红书的笔记图绝不能进任何交付物。** 那是个人创作者的作品，搬运和洗稿的
法律风险是实的。内部参考可以，出现在给客户的东西里不行。

## 主管交过来的图，怎么标出处

**别让人去填 CSV。** 在那个文件夹里放一个 `来源.txt`，两行就够：

```
渠道：wgsn
来源：WGSN AW27 Knitwear Key Items（陈主管 09-08 给）
链接：https://www.wgsn.com/fashion/article/999
作者：WGSN
```

中英文冒号都认，`source.txt` 也认。只写一行不带前缀的，当成来源说明。
想逐张给链接，再加这样的行：

```
003.jpg = https://www.wgsn.com/fashion/article/1001
```

`渠道：` 那行会覆盖命令行的 `--channel`，所以主管在 txt 里写清楚了，
收的人就不用记该传什么参数。

脚本产出的目录（`brand_collect.mjs` 的输出）继续用 `sources.csv`，两者都有时
CSV 更精确，压过 `来源.txt`。

### 没见过的渠道

陈主管给的图来自某个没查过的站（比如 WOW），照收，但会：

```
提醒：渠道「wow」不在已知渠道表里 —— 授权边界没查过，
      这批图标成 license_checked=false，用之前得先确认能不能用
```

台账里 `license_checked: false`，`--check` 每次都会点名，直到有人确认了授权、
把这个渠道加进 `collect.py` 的 `USE` 表。**没查过就不能假设能用。**

### 批次链接不冒充逐张出处

`来源.txt` 里的 `链接：` 记进 `batch_url`，**不写进 `source_url`**。
它说的是「这批图来自哪份报告」，不是「这张图在哪」。混进去会让出处完整度
显示成逐张都有，Sources 页那时就写不出东西了。

## 台账怎么给人看

```
python -X utf8 scripts/collect.py --plan plan.json --export-csv 台账.csv
```

`ledger.json` 是给脚本读的。要给陈主管核对出处，导成 CSV（带 UTF-8 BOM，
Excel 直接开不乱码），第一列就是「能否交付」。

## 对账

```
python -X utf8 scripts/collect.py --plan plan.json --check
```

台账有、磁盘没有 / 磁盘有、台账没有 / 没有出处——三种都报。**磁盘上多出来的图
是最危险的一种**：它没有出处，很容易在后面被误当成可交付素材。

## 边界

- `use: internal` 的图（brand / wgsn / pinterest 全都是）**不得进客户交付页**。
  交付页上的图只能来自节点 05 自己生成的（`use: deliverable`）。
- 收图前先看 `--status` 对配额，别把一个方向收爆、另一个空着。
- 不要手动往素材目录里丢文件——绕过脚本就没有台账，`--check` 会把它揪出来。
