# WGSN / Fashion Snoops 选品清单
**REQ-2026-0908-SEED · Seed 女装 秋冬趋势与款式推荐**

给做这件事的同事。佘吉不登录、不抓取——目标 **21 张**，
收完放进 `02-assets/` 对应方向的目录，再跑 collect.py 收进台账。

> **订阅制，ToS 禁止抓取**

佘吉只产出选片清单（栏目路径 + 关键词 + 目标张数），由持账号的同事手动导出后放进 02 的素材目录。

## 搜什么

按方向分，每个方向的额度均摊：

### Elevated Everyday　精致基础　约 4 张
- `merino womenswear sweater AW27`
- `fine gauge womenswear sweater AW27`
- `tonal womenswear sweater AW27`
- `crew neck womenswear sweater AW27`

### Oversized Comfort　宽松舒适　约 4 张
- `oversized womenswear sweater AW27`
- `drop shoulder womenswear sweater AW27`
- `chunky gauge womenswear sweater AW27`
- `cocoon shape womenswear sweater AW27`

### Textural Craft　肌理手作　约 4 张
- `cable knit womenswear sweater AW27`
- `pointelle womenswear sweater AW27`
- `bobble womenswear sweater AW27`
- `brushed mohair womenswear sweater AW27`

### Sporty Layer　运动叠穿　约 4 张
- `half zip womenswear sweater AW27`
- `funnel neck womenswear sweater AW27`
- `colour blocking womenswear sweater AW27`
- `raglan sleeve womenswear sweater AW27`

### Statement Colour　色彩表达　约 4 张
- `intarsia womenswear sweater AW27`
- `stripe womenswear sweater AW27`
- `argyle womenswear sweater AW27`
- `saturated womenswear sweater AW27`


## 怎么挑

- 筛 Southern Hemisphere / Australia —— 站内默认北半球口径
- 记下：报告名 + 发布日期、栏目路径、图片 ID 或页面 URL
- 只作方向和克重的校准，不当素材用

## 出处怎么记

在导出目录放一份 `sources.csv`，**逐张**登记：

```csv
filename,source_url,author,note
001.jpg,<链接>,<作者或店铺>,<标题>
```

没有它，出处只记到批次级别，Sources 页写不出单张来源。
