# -*- coding: utf-8 -*-
"""PChome × MOMO 商品價格比較系統
輸入產品名稱與頁數 -> 兩個平台各自爬取 -> 折線圖 / 圓餅圖 / 旭日圖 / 柱狀圖比較
"""
import re
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from curl_cffi import requests as cffi_requests  # MOMO 需要模擬 Chrome 的 TLS 指紋

# ================================================================ 設定
PCHOME_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PCHOME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

MOMO_HOME = "https://www.momoshop.com.tw/"
MOMO_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
MOMO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}

PRICE_EDGES = [0, 300, 500, 1000, 1500, 2000, 3000, 5000, float("inf")]
PRICE_LABELS = [
    "300以下", "300-499", "500-999", "1,000-1,499",
    "1,500-1,999", "2,000-2,999", "3,000-4,999", "5,000以上",
]
COLORS = {"PChome": "#EF6C00", "MOMO": "#C2185B"}


# ================================================================ 爬蟲
@st.cache_data(ttl=600, show_spinner=False)
def fetch_pchome(keyword: str, page: int) -> dict:
    params = {"q": keyword, "page": page, "sort": "sale/dc"}
    resp = requests.get(PCHOME_API, params=params, headers=PCHOME_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    rows = [
        {"品名": p.get("name", ""), "價格": p.get("price")}
        for p in (data.get("prods") or [])
    ]
    return {"rows": rows, "total_page": data.get("totalPage")}


def momo_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


@st.cache_data(ttl=600, show_spinner=False)
def fetch_momo(keyword: str, page: int) -> dict:
    session = cffi_requests.Session(impersonate="chrome")
    session.get(MOMO_HOME, headers=MOMO_HEADERS, timeout=20)  # 先取得 cookie
    resp = session.post(
        MOMO_API, headers=MOMO_HEADERS, json=momo_payload(keyword, page), timeout=20
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}：{resp.text[:150]}")
    goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", []) or []
    rows = [
        {"品名": g.get("goodsName", ""), "價格": g.get("goodsPrice", "")} for g in goods
    ]
    return {"rows": rows, "total_page": None}


FETCHERS = {"PChome": fetch_pchome, "MOMO": fetch_momo}


def crawl(platform, keyword, pages, delay, bar, offset, total):
    """爬取單一平台多頁；回傳 (rows, 提示訊息)。"""
    fetch = FETCHERS[platform]
    rows, note = [], None
    for i in range(1, pages + 1):
        bar.progress((offset + i - 1) / total, text=f"{platform}：第 {i} / {pages} 頁...")
        data = fetch(keyword, i)
        if not data["rows"]:
            note = f"{platform} 第 {i} 頁沒有資料，已提前結束。"
            break
        rows += data["rows"]
        tp = data.get("total_page")
        if tp and i >= int(tp):
            if i < pages:
                note = f"{platform} 此關鍵字只有 {tp} 頁，已全部抓完。"
            break
        if i < pages:
            time.sleep(delay)
    return rows, note


# ================================================================ 資料整理
def guess_brand(name: str) -> str:
    """從商品名稱粗略推測品牌：優先取開頭【】或[]內文字，否則取第一個詞。"""
    name = str(name).strip()
    m = re.match(r"^[【\[（(]([^】\]）)]{1,15})[】\]）)]", name)
    if m:
        return m.group(1).strip()
    token = re.split(r"[\s\u3000/／\-－_＿]+", name)[0]
    return token[:12] or "未知"


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["價格"] = pd.to_numeric(
        out["價格"].astype(str).str.replace(r"[^\d]", "", regex=True), errors="coerce"
    )
    out = out.dropna(subset=["價格"]).reset_index(drop=True)
    out["價格"] = out["價格"].astype(int)
    out["序號"] = out.groupby("平台").cumcount() + 1  # 各平台各自的銷售/搜尋排序
    out["價格帶"] = pd.cut(out["價格"], bins=PRICE_EDGES, labels=PRICE_LABELS, right=False)
    out["品牌(推測)"] = out["品名"].map(guess_brand)
    return out


# ================================================================ 畫面
st.set_page_config(page_title="PChome × MOMO 價格比較", layout="wide")
st.title("PChome × MOMO 商品價格比較")

with st.form("search_form"):
    c1, c2, c3 = st.columns([3, 1, 1])
    keyword = c1.text_input("產品名稱", value="行動電源")
    pages = c2.number_input("每個平台爬取頁數", min_value=1, max_value=20, value=2, step=1)
    delay = c3.number_input("每頁間隔(秒)", min_value=0.0, max_value=10.0, value=1.0, step=0.5)
    platforms = st.multiselect("比較平台", list(FETCHERS), default=list(FETCHERS))
    submitted = st.form_submit_button("開始爬取並比較", type="primary")

if submitted:
    kw = keyword.strip()
    if not kw:
        st.warning("請輸入產品名稱")
    elif not platforms:
        st.warning("請至少選擇一個平台")
    else:
        total = int(pages) * len(platforms)
        bar = st.progress(0.0, text="準備開始...")
        all_rows, errors, notes = [], {}, []
        for idx, plat in enumerate(platforms):
            try:
                rows, note = crawl(plat, kw, int(pages), float(delay), bar, idx * int(pages), total)
                all_rows += [{"平台": plat, **r} for r in rows]
                if note:
                    notes.append(note)
            except Exception as e:  # 單一平台失敗不影響另一個平台
                errors[plat] = str(e)
        bar.empty()

        st.session_state["errors"] = errors
        st.session_state["notes"] = notes
        df_new = prepare(pd.DataFrame(all_rows)) if all_rows else pd.DataFrame()
        if df_new.empty:
            st.session_state.pop("df", None)
        else:
            st.session_state["df"] = df_new
            st.session_state["keyword"] = kw

for plat, msg in st.session_state.get("errors", {}).items():
    st.warning(f"{plat} 爬取失敗：{msg}")
for note in st.session_state.get("notes", []):
    st.info(note)

if "df" not in st.session_state:
    st.info("輸入產品名稱與頁數後，按「開始爬取並比較」。")
    st.stop()

df = st.session_state["df"]
kw = st.session_state["keyword"]
plats = [p for p in FETCHERS if p in set(df["平台"])]

# ---- 統計比較表
stats = (
    df.groupby("平台")["價格"]
    .agg(商品數="count", 平均價="mean", 中位數="median", 最高價="max", 最低價="min")
    .round(0).astype(int).reset_index()
)
cols = st.columns(len(plats))
for col, plat in zip(cols, plats):
    s = stats[stats["平台"] == plat].iloc[0]
    col.subheader(plat)
    col.metric("商品數", f"{s['商品數']:,}")
    col.metric("平均價", f"{s['平均價']:,}")
    col.caption(f"中位數 {s['中位數']:,}　最高 {s['最高價']:,}　最低 {s['最低價']:,}")

with st.sidebar:
    st.header("圖表設定")
    hi = max(int(df.groupby("平台").size().max()), 11)
    n_line = st.slider("折線圖：每平台顯示前幾筆", 10, hi, min(70, hi))
    top_brand = st.slider("旭日圖：顯示前幾大品牌", 3, 15, 8)

tab1, tab2, tab3, tab4, tab5 = st.tabs(["折線圖", "圓餅圖", "旭日圖", "柱狀圖", "資料表"])

# ---- 折線圖：兩平台價格走勢 + 各自平均線
with tab1:
    df_line = df[df["序號"] <= n_line]
    fig = px.line(
        df_line, x="序號", y="價格", color="平台", markers=True, hover_name="品名",
        color_discrete_map=COLORS, labels={"序號": "商品序號（依平台排序）"},
        title=f"「{kw}」售價走勢比較",
    )
    for _, r in stats.iterrows():
        fig.add_hline(
            y=r["平均價"], line_dash="dash", line_color=COLORS.get(r["平台"], "gray"),
            annotation_text=f"{r['平台']} 平均 {r['平均價']:,}", annotation_position="top left",
        )
    st.plotly_chart(fig, key="line")

# ---- 圓餅圖：各平台價格帶佔比
with tab2:
    pie_cols = st.columns(len(plats))
    for col, plat in zip(pie_cols, plats):
        band = (
            df[df["平台"] == plat]
            .groupby("價格帶", observed=True).size().reset_index(name="商品數")
        )
        fig = px.pie(band, names="價格帶", values="商品數", title=f"{plat} 價格帶佔比")
        fig.update_traces(sort=False, textinfo="percent+label")
        col.plotly_chart(fig, key=f"pie_{plat}")

# ---- 旭日圖：平台 -> 價格帶 -> 品牌
with tab3:
    top = df["品牌(推測)"].value_counts().head(top_brand).index
    sb = df.copy()
    sb["品牌(推測)"] = sb["品牌(推測)"].where(sb["品牌(推測)"].isin(top), "其他")
    sb = (
        sb.groupby(["平台", "價格帶", "品牌(推測)"], observed=True)
        .size().reset_index(name="商品數")
    )
    fig = px.sunburst(
        sb, path=["平台", "價格帶", "品牌(推測)"], values="商品數",
        title=f"「{kw}」平台 → 價格帶 → 品牌分布",
    )
    st.plotly_chart(fig, key="sunburst")
    st.caption("品牌是依商品名稱開頭推測的，可能不完全準確。")

# ---- 柱狀圖：價格帶商品數比較 + 價格統計比較
with tab4:
    band_cnt = df.groupby(["平台", "價格帶"], observed=True).size().reset_index(name="商品數")
    fig = px.bar(
        band_cnt, x="價格帶", y="商品數", color="平台", barmode="group",
        color_discrete_map=COLORS, category_orders={"價格帶": PRICE_LABELS},
        title="各價格帶商品數比較",
    )
    st.plotly_chart(fig, key="bar_band")

    melt = stats.melt(
        id_vars="平台", value_vars=["平均價", "中位數", "最高價", "最低價"],
        var_name="指標", value_name="價格",
    )
    fig = px.bar(
        melt, x="指標", y="價格", color="平台", barmode="group", text_auto=",",
        color_discrete_map=COLORS, title="價格統計比較",
    )
    st.plotly_chart(fig, key="bar_stats")

# ---- 資料表與下載
with tab5:
    st.dataframe(stats, hide_index=True)
    st.dataframe(
        df[["平台", "序號", "品名", "價格", "價格帶", "品牌(推測)"]], hide_index=True
    )
    st.download_button(
        "下載 CSV",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"價格比較_{datetime.now():%Y%m%d}_{kw}.csv",
        mime="text/csv",
    )
