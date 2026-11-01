# -*- coding: utf-8 -*-
"""
找剧AI (zhaoju3.ai) —— TVBox py 源
逆向要点（2026-11-01 实测）：
  ① 请求签名：HMAC-SHA256，key=明文内嵌 bundle 的 hex 串（当 ASCII 字符串用，勿 hex 解码）
     msg = "{METHOD}\n{path}\n{timestamp}\n{nonce}"  ← 分隔符是 LF(\n)，不是 CRLF
     头：x-ai-movie-timestamp / x-ai-movie-nonce / x-ai-movie-signature
  ② 首页：GET /v1/feed/home
  ③ 分类/搜索：GET /v1/browse/catalog?kind=&intent=latest_catalog&genre=&sort=&page=&limit=
     搜索 = 同接口加 q=关键词（服务端自动 intent=catalog_search）
  ④ 详情：GET /v1/catalog/{id} → title/year/area/actors/directors/poster/description + episodes[{number,token}]
  ⑤ 播放：GET /v1/playback/resolve/{token}?content_kind=&episode_index=&episode_count=
     → line_options[] 每行 {label, url, url_kind, resolve_mode, resolve_required, playback_source_id}
       直链行 url_kind=m3u8/mp4 直接可播；解析行 url=resolve://ticket → POST /v1/playback/resolve-line {ticket}
  游客账号即可浏览+取直链，无需注册登录。
"""
import json
import re
import time
import hmac
import hashlib
import os
import random
import string
import base64
import requests
from urllib.parse import urljoin, quote

# 壳端基类：兼容 base.spider；妖灵/XOT 壳的 chaquopy lxml 缺 etree 时回退到裸类
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    try:
        from base.spider import BaseSpider as _BaseSpider
    except Exception:
        class _BaseSpider(object):
            def init(self, extend=""):
                pass


class Spider(_BaseSpider):

    HOST = "https://zhaoju3.ai"
    SIGN_KEY = "8f18c8e472a9fefbab7108f5c447f69b48a4b5d34f662ed83163167fc10fb3b8"
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # 频道 → kind 映射（与站点 runtime bootstrap 一致）
    CHANNELS = [
        ("recommend", "AI找剧"),
        ("updates", "最近更新"),
        ("series", "电视剧"),
        ("movie", "电影"),
        ("anime", "动漫"),
        ("variety", "综艺"),
        ("short_drama", "短剧"),
    ]

    # 每个 kind 的筛选：地区 + 年份（按站点真实可用，通用兜底）
    KIND_GENRES = {
        "movie": ["喜剧", "爱情", "动作", "犯罪", "科幻", "奇幻", "冒险", "灾难",
                  "恐怖", "惊悚", "剧情", "战争", "歌舞", "悬疑", "动画"],
        "series": ["国产剧", "剧情", "爱情", "古装", "悬疑", "都市", "喜剧", "战争"],
        "anime": ["国产动漫", "日本动漫", "热血", "搞笑", "恋爱", "奇幻"],
        "variety": ["真人秀", "音乐", "访谈", "喜剧", "美食", "竞技"],
    }
    SORTS = [("最热", "heat_desc"), ("最新", "time"), ("评分", "score")]

    def getName(self):
        return "找剧AI"

    def getDependence(self):
        # 妖灵/XOT 壳要求：返回源依赖的 Python 包名（无依赖返回空列表）
        return []

    # ================= 初始化 =================
    def init(self, extend=""):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.UA,
            "Accept": "application/json",
            "Origin": self.HOST,
            "Referer": self.HOST + "/",
        })
        # ext 可覆盖域名（换域名自愈用）
        raw = str(extend or "").strip()
        if raw.startswith("http"):
            self.HOST = raw.rstrip("/")
        self._line_cache = {}   # token -> line_options 缓存
        self._filters = None

    # ================= 请求（带签名） =================
    def _sign_headers(self, method, path):
        ts = str(int(time.time() * 1000))
        nonce = os.urandom(16).hex()
        msg = "%s\n%s\n%s\n%s" % (method.upper(), path, ts, nonce)
        sig = hmac.new(self.SIGN_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
        return {
            "x-ai-movie-timestamp": ts,
            "x-ai-movie-nonce": nonce,
            "x-ai-movie-signature": sig,
        }

    def _get(self, path, timeout=(8, 15)):
        url = self.HOST + path
        h = dict(self.session.headers)
        h.update(self._sign_headers("GET", path))
        for i in range(4):
            try:
                r = self.session.get(url, headers=h, timeout=timeout)
                if 200 <= r.status_code < 300:
                    return r.json()
                # 429/5xx/SSL 重置 → 退避重试
                time.sleep(1.0 * (i + 1))
            except Exception:
                time.sleep(1.0 * (i + 1))
        return {}

    def _post(self, path, body, timeout=(8, 18)):
        url = self.HOST + path
        h = dict(self.session.headers)
        h.update(self._sign_headers("POST", path))
        h["Content-Type"] = "application/json"
        for i in range(4):
            try:
                r = self.session.post(url, headers=h, json=body, timeout=timeout)
                if 200 <= r.status_code < 300:
                    return r.json()
                time.sleep(1.0 * (i + 1))
            except Exception:
                time.sleep(1.0 * (i + 1))
        return {}

    # ================= 工具 =================
    @staticmethod
    def _clean(s):
        return str(s or "").replace("#", " ").replace("$", " ").replace("\n", " ").strip()

    def _card_item(self, c):
        """browse/feed 卡片 → vod 列表项"""
        cid = str(c.get("id") or c.get("variant_id") or c.get("selected_variant_id") or "")
        if not cid:
            return None
        name = str(c.get("title") or c.get("normalized_title") or "").strip()
        if not name:
            return None
        remark = self._clean(c.get("remarks") or "")
        if not remark:
            sc = c.get("score")
            remark = ("%.1f分" % sc) if isinstance(sc, (int, float)) else ""
        return {
            "vod_id": cid,
            "vod_name": name,
            "vod_pic": str(c.get("poster_url") or ""),
            "vod_remarks": remark,
        }

    # ================= 首页分类 =================
    def homeContent(self, filter):
        classes = [{"type_id": k, "type_name": v} for k, v in self.CHANNELS]
        filters = {}
        for k, _ in self.CHANNELS:
            if k not in self.KIND_GENRES:
                filters[k] = [{"key": "sort", "name": "排序",
                               "value": [{"n": "全部", "v": ""}] +
                                        [{"n": n, "v": v} for n, v in self.SORTS]}]
                continue
            groups = []
            genres = self.KIND_GENRES[k]
            if genres:
                groups.append({"key": "genre", "name": "分类",
                               "value": [{"n": "全部", "v": ""}] +
                                        [{"n": g, "v": g} for g in genres]})
            groups.append({"key": "sort", "name": "排序",
                           "value": [{"n": "全部", "v": ""}] +
                                    [{"n": n, "v": v} for n, v in self.SORTS]})
            filters[k] = groups
        return {"class": classes, "filters": filters}

    def homeVideoContent(self):
        data = self._get("/v1/feed/home")
        items = []
        for sec in (data.get("sections") or []):
            for c in (sec.get("cards") or []):
                it = self._card_item(c)
                if it:
                    items.append(it)
        return {"list": items}

    # ================= 分类列表 =================
    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1

        kind = str(tid or "").strip() or "movie"
        params = {"page": pg, "limit": 24}
        if kind == "recommend":
            # AI找剧：用首页 feed
            return self.homeVideoContent()
        if kind == "updates":
            params["intent"] = "latest_catalog"
        else:
            params["kind"] = kind
            params["intent"] = "latest_catalog"

        # 解析筛选参数
        ext = extend
        if isinstance(ext, str):
            s = ext.strip()
            if s.startswith("{"):
                try:
                    ext = json.loads(s)
                except Exception:
                    ext = {}
            elif "=" in s:
                try:
                    ext = dict(kv.split("=", 1) for kv in s.split("&") if "=" in kv)
                except Exception:
                    ext = {}
            else:
                ext = {}
        if isinstance(ext, dict):
            for k in ("genre", "sort"):
                v = str(ext.get(k) or "").strip()
                if v and v != "全部":
                    params[k] = v

        data = self._get("/v1/browse/catalog?" + self._qs(params))
        cards = data.get("cards") or []
        items = [it for it in (self._card_item(c) for c in cards) if it]
        total = 0
        pg_info = data.get("pagination") or {}
        try:
            total = int(pg_info.get("total") or len(items))
        except Exception:
            total = len(items)
        has_more = bool(pg_info.get("has_more", False))
        pagecount = (total + 23) // 24 if total > 0 else pg
        if has_more and pagecount <= pg:
            pagecount = pg + 1
        return {"page": pg, "pagecount": pagecount, "limit": 24,
                "total": total, "list": items}

    @staticmethod
    def _qs(params):
        from urllib.parse import urlencode
        return urlencode({k: v for k, v in params.items() if v not in (None, "")})

    # ================= 详情 =================
    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, list) and ids else ids
        vid = str(vid or "").strip()
        if not vid:
            return {"list": []}
        d = self._get("/v1/catalog/" + vid)
        if not isinstance(d, dict) or not d:
            return {"list": []}

        content_kind = str(d.get("content_kind") or "series")
        episodes = d.get("episodes") or []
        total_eps = len(episodes)

        # 解析线路：解析第 1 集拿 line_options
        lines = self._resolve_lines(episodes, d)

        play_from, play_url = [], []
        # 每集 id 打包：集号|总集数|token，供 playerContent 正确解析
        ep_str = "#".join(
            "%s$%s|%s|%s" % (self._clean(e.get("title") or ("第%d集" % (e.get("number") or i + 1))),
                             e.get("number") or (i + 1), total_eps,
                             str(e.get("token") or ""))
            for i, e in enumerate(episodes) if (e.get("token"))
        )
        if lines:
            for lb in lines:
                play_from.append(lb)
                play_url.append(ep_str)

        if not play_from:
            # 兜底：单线路，直接给 token
            if ep_str:
                play_from = ["找剧AI"]
                play_url = [ep_str]

        remark = self._clean(d.get("remarks") or "")
        tag = " / ".join(self._clean(g) for g in (d.get("genres") or [])[:3])
        if tag and remark:
            remark = tag + " · " + remark
        elif tag:
            remark = tag

        vod = {
            "vod_id": vid,
            "vod_name": str(d.get("title") or d.get("normalized_title") or ""),
            "vod_pic": str(d.get("poster_url") or ""),
            "vod_content": re.sub(r"<[^>]+>", "", str(d.get("description") or "")).strip(),
            "vod_remarks": remark,
            "vod_year": str(d.get("year") or ""),
            "vod_area": str(d.get("area") or ""),
            "vod_actor": " / ".join(str(a) for a in (d.get("actors") or [])[:6]),
            "vod_director": " / ".join(str(a) for a in (d.get("directors") or [])),
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        return {"list": [vod]}

    def _resolve_lines(self, episodes, detail):
        """解析第 1 集的 token → 线路 label 列表（只保留可播的）"""
        if not episodes:
            return []
        ep0 = episodes[0]
        token = str(ep0.get("token") or "")
        if not token:
            return []
        content_kind = str(detail.get("content_kind") or "series")
        total = len(episodes)
        idx = int(ep0.get("number") or 1)
        los = self._resolve(token, content_kind, idx, total)
        labels = []
        for lo in los:
            url = str(lo.get("url") or "")
            # 只保留直链行（m3u8/mp4 直链）；跳过 resolve:// 解析线（抖音官方线→签名 mp4，壳端 403）
            if url.startswith("http"):
                labels.append(self._clean(lo.get("label") or lo.get("playback_source_id") or "线路"))
        return labels

    def _resolve(self, token, content_kind, ep_index, ep_count):
        """GET /v1/playback/resolve/{token} → line_options"""
        if token in self._line_cache:
            return self._line_cache[token]
        from urllib.parse import urlencode, quote
        q = urlencode({
            "content_kind": content_kind,
            "episode_index": str(ep_index),
            "episode_count": str(ep_count),
        })
        path = "/v1/playback/resolve/%s?%s" % (quote(token, safe=""), q)
        data = self._get(path)
        los = data.get("line_options") or []
        if los:
            self._line_cache[token] = los
        return los

    # ================= 搜索 =================
    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        kw = str(key or "").strip()
        if not kw:
            return {"page": pg, "pagecount": pg, "limit": 20, "total": 0, "list": []}
        data = self._get("/v1/browse/catalog?" + self._qs({"q": kw, "page": pg, "limit": 20}))
        cards = data.get("cards") or []
        items = [it for it in (self._card_item(c) for c in cards) if it]
        pg_info = data.get("pagination") or {}
        try:
            total = int(pg_info.get("total") or len(items))
        except Exception:
            total = len(items)
        pagecount = (total + 19) // 20 if total > 0 else pg
        return {"page": pg, "pagecount": pagecount, "limit": 20,
                "total": total, "list": items}

    def searchContentPage(self, key, quick, page):
        return self.searchContent(key, quick, page)

    # ================= 播放 =================
    def playerContent(self, flag, id, vipFlags=None):
        token = str(id or "").strip()
        if not token:
            return {"parse": 0, "url": "", "playUrl": "", "header": ""}

        # 已是直链
        if token.startswith("http://") or token.startswith("https://"):
            return {"parse": 0, "url": token, "playUrl": token, "header": ""}

        # id 打包：ep_index|ep_count|token（详情页写入）；flag = 线路名
        label = str(flag or "").strip()
        ep_index, ep_count = 1, 1
        if "|" in token:
            parts = token.split("|")
            if len(parts) >= 3:
                try:
                    ep_index = int(parts[0])
                    ep_count = int(parts[1])
                except Exception:
                    ep_index, ep_count = 1, 1
                token = parts[2]

        # 解析该集 token → line_options
        los = self._line_cache.get(token)
        if not los:
            los = self._resolve(token, "series", ep_index, ep_count) \
                  or self._resolve(token, "movie", ep_index, ep_count)

        # 选中目标线路（flag = 线路名）
        line = None
        for lo in los or []:
            if label and (self._clean(lo.get("label")) == label
                          or str(lo.get("playback_source_id")) == label):
                line = lo
                break
        if line is None and los:
            # 没匹配到 → 优先第一个直链
            for lo in los:
                u = str(lo.get("url") or "")
                if u.startswith("http"):
                    line = lo
                    break
            if line is None:
                line = los[0]

        if not line:
            return {"parse": 0, "url": "", "playUrl": "", "header": ""}

        url = str(line.get("url") or "")
        # 解析型线路：resolve://ticket → 二次解析
        if url.startswith("resolve://"):
            ticket = url[len("resolve://"):]
            rd = self._post("/v1/playback/resolve-line?view=compact", {"ticket": ticket})
            ln = rd.get("line") or {}
            url = str(ln.get("url") or "")
        elif str(line.get("url_kind") or "") == "resolve_ticket":
            rd = self._post("/v1/playback/resolve-line?view=compact", {"ticket": url})
            ln = rd.get("line") or {}
            url = str(ln.get("url") or "")

        # 解析失败 → 兜底取第一条直链线路
        if not url.startswith("http") and los:
            for lo in los:
                u = str(lo.get("url") or "")
                if u.startswith("http"):
                    url = u
                    break

        if not url.startswith("http"):
            return {"parse": 0, "url": "", "playUrl": "", "header": ""}

        # m3u8 用本地代理改写相对路径（媒体/分片/AES key），绕开壳端 HlsProxy 断裂
        if ".m3u8" in url.lower():
            url = self._proxy_wrap(url, "m3u8")

        hdr = {"User-Agent": self.UA, "Referer": self.HOST + "/"}
        return {"parse": 0, "url": url, "playUrl": url, "header": hdr}

    def _media_m3u8(self, url):
        """主播放列表(含 EXT-X-STREAM-INF) → 返回第一条媒体播放列表的绝对 URL；否则原样返回"""
        try:
            r = self.session.get(url, timeout=(8, 18))
            if r.status_code != 200:
                return url
            txt = r.text
            if "#EXT-X-STREAM-INF" not in txt:
                return url
            for line in txt.splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    return urljoin(url, s)
            return url
        except Exception:
            return url

    def isVideoFormat(self, url):
        u = str(url or "").lower()
        return any(f in u for f in (".m3u8", ".mp4", ".flv", ".ts", ".m3u8?"))

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        try:
            d = json.loads(param) if isinstance(param, str) else dict(param or {})
            url = str(d.get("url") or d.get("u") or "")
            type_ = str(d.get("type") or "")
            # 壳端会把 proxy 基址误拼回 url 末尾（<m3u8>http://127.0.0.1:9978/proxy?do=py），剥离掉
            if url:
                idx = url.find("http", 1)
                if idx > 0:
                    url = url[:idx]
            if not url.startswith("http"):
                return [404, "text/plain", b""]

            # m3u8：改写相对 URL（媒体/分片/AES key）为绝对，喂给播放器
            if type_ == "m3u8" or ".m3u8" in url:
                content = self._rewrite_m3u8_content(url)
                if content:
                    return [200, "application/vnd.apple.mpegurl; charset=utf-8",
                            content.encode("utf-8"), {}]

            r = self.session.get(url, headers={"User-Agent": self.UA,
                                               "Referer": self.HOST + "/"},
                                 timeout=(8, 20), verify=False)
            if r.status_code != 200:
                return [404, "text/plain", b""]
            ct = r.headers.get("Content-Type") or "application/octet-stream"
            return [200, ct, r.content, {}]
        except Exception:
            return [404, "text/plain", b""]

    def _proxy_base(self):
        try:
            if hasattr(self, "getProxyUrl"):
                p = self.getProxyUrl()
                if p and str(p).startswith("http"):
                    return str(p)
        except Exception:
            pass
        return "http://127.0.0.1:9978/proxy?do=py"

    def _proxy_wrap(self, url, type_="m3u8"):
        base = self._proxy_base()
        if not base:
            return url
        sep = "&" if "?" in base else "?"
        return base + sep + "type=" + type_ + "&url=" + quote(url, safe="")

    def _rewrite_m3u8_content(self, url):
        """抓 m3u8，把相对 URL（媒体/分片/AES key）改成绝对，返回改写文本；失败 None"""
        try:
            r = self.session.get(url, timeout=(8, 18))
            if r.status_code != 200:
                return None
            txt = r.text
            if not txt.lstrip().startswith("#EXTM3U"):
                return None
            out = []
            for line in txt.splitlines():
                s = line.strip()
                if not s:
                    out.append("")
                    continue
                if s.startswith("#EXT-X-KEY"):
                    m = re.search(r'URI="([^"]+)"', s)
                    if m and not m.group(1).startswith("http"):
                        s = s.replace(m.group(1), urljoin(url, m.group(1)))
                elif not s.startswith("#") and not s.lower().startswith("http"):
                    s = urljoin(url, s)
                out.append(s)
            return "\n".join(out)
        except Exception:
            return None

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass
