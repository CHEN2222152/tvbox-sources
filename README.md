# 找剧AI (zhaoju3.ai) — TVBox Python 影视源

纯 Python 源，无需网盘即可播放。对接 https://zhaoju3.ai/ （找剧AI）全站数据：首页、分类、搜索、详情、播放直链。

- 自签名 HMAC API（逆向自站点前端）
- 游客账号即用，无需登录
- 返回直链 / m3u8（含相对路径重写，壳端 localProxy 直接出画）

## 文件

| 文件 | 说明 |
|---|---|
| `zhaoju_ai.py` | 影视源本体（单文件，自包含） |

## 加速地址（国内推荐）

同一个文件，多个镜像，哪个快用哪个。**推荐用 jsdelivr**（CDN 自动镜像 GitHub，国内最稳）。

| 镜像 | 地址 |
|---|---|
| ⭐ jsdelivr（推荐） | `https://cdn.jsdelivr.net/gh/CHEN2222152/tvbox-sources@main/zhaoju_ai.py` |
| 直连 raw | `https://raw.githubusercontent.com/CHEN2222152/tvbox-sources/main/zhaoju_ai.py` |
| ghproxy 反代 | `https://ghproxy.net/https://raw.githubusercontent.com/CHEN2222152/tvbox-sources/main/zhaoju_ai.py` |
| wget.la 反代 | `https://wget.la/raw.githubusercontent.com/CHEN2222152/tvbox-sources/main/zhaoju_ai.py` |

## 安装（妖灵 / XOT）

把上面任一地址填进站点 `api`：

```json
{"key":"zhaoju","name":"🎈找剧AI丨蓝光","type":3,
 "api":"https://cdn.jsdelivr.net/gh/CHEN2222152/tvbox-sources@main/zhaoju_ai.py",
 "searchable":1,"filterable":1}
```

> 要求壳支持 `localProxy` + `getProxyUrl`（妖灵/XOT 系）。别催了/langhuan 系 HlsProxy 会截断 proxy query，暂不支持播放。

## 接口

- 首页：`/v1/feed/home`
- 分类：`/v1/browse/catalog`
- 搜索：`/v1/browse/catalog?q=`
- 详情：`/v1/catalog/{id}`
- 播放：`/v1/playback/resolve/{token}`
