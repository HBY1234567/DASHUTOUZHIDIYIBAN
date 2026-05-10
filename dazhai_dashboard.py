import streamlit as st
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os

st.set_page_config(page_title="大数投资看板 (最终版)", layout="wide")
st.markdown(
    """
    <style>
        .reportview-container .main .block-container {
            max-width: 90%;
            padding-top: 2rem;
            padding-right: 1rem;
            padding-left: 1rem;
        }
        .stDataFrame table td, .stDataFrame table th {
            text-align: center !important;
        }
        .main-advice {
            background-color: #e6f7ff;
            border-left: 4px solid #1890ff;
            padding: 8px;
            margin: 5px 0;
        }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("📈 大数投资选股看板")
st.markdown("点击代码列旁的📋复制代码 | 同行业第一名标红，第二名标橙 | **核心卖出规则：从52周最低点上涨100%时卖出**")

CACHE_DIR = "./.stock_cache"
os.makedirs(CACHE_DIR, exist_ok=True)
HIGH_LOW_CACHE_FILE = os.path.join(CACHE_DIR, "52w_high_low.json")
REALTIME_CACHE_FILE = os.path.join(CACHE_DIR, "realtime_market.parquet")
INDUSTRY_CACHE_FILE = os.path.join(CACHE_DIR, "industry_data.parquet")
PORTFOLIO_FILE = os.path.join(CACHE_DIR, "portfolio.json")

# ========== 持仓持久化 ==========
def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, 'r') as f:
            data = json.load(f)
            return pd.DataFrame(data)
    else:
        return pd.DataFrame(columns=['代码', '名称', '行业', '买入价', '股数', '买入日期', '备注'])

def save_portfolio(df):
    df.to_json(PORTFOLIO_FILE, orient='records', date_format='iso')
    st.session_state.portfolio = df

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = load_portfolio()

# ========== 侧边栏 ==========
st.sidebar.header("🔧 筛选与数据管理")

pe_max = st.sidebar.slider("市盈率 (PE) 上限", 5, 30, 20, 1, help="大数投资建议 PE<20")
pb_max = st.sidebar.slider("市净率 (PB) 上限", 0.5, 3.0, 2.0, 0.1, help="大数投资建议 PB<2")
year_gain_warning = st.sidebar.slider("年内涨幅预警阈值 (%)", 50, 200, 100, 10)
alert_drop = st.sidebar.slider("加仓预警：下跌幅度（相对买入价）", 5, 30, 10, 1, help="大数投资建议每跌10-15%加一档")
max_single_weight = st.sidebar.slider("单只股票最大仓位比例 (%)", 1, 20, 5, 1, help="大数投资建议单只股票不超过总资金的5%")

st.sidebar.subheader("高低点位置调整（仅辅助参考）")
buy_position_threshold = st.sidebar.slider("买入推荐阈值（股价低于52周低点的百分比）", 0, 30, 10, 1)
sell_position_threshold = st.sidebar.slider("卖出推荐阈值（股价高于52周高点的百分比）", 0, 30, 10, 1)

st.sidebar.markdown("---")
st.sidebar.caption("📌 **大数投资核心规则**：\n- 选股：PE<20, PB<2\n- 卖出：从52周最低点上涨100%\n- 仓位：单只≤5%，行业分散\n- 分档：每跌10-15%加仓")

st.sidebar.subheader("📊 52周数据管理")
if os.path.exists(HIGH_LOW_CACHE_FILE):
    with open(HIGH_LOW_CACHE_FILE, 'r') as f:
        cache_cnt = len(json.load(f))
    st.sidebar.caption(f"缓存中有 {cache_cnt} 只股票的52周数据")
else:
    st.sidebar.caption("暂无52周数据缓存")

sell_mode = st.sidebar.selectbox("卖出规则（主规则：经典翻倍）",
    options=["经典翻倍（52周低点+100%）", "PB估值卖出（PB>阈值）", "位置比例卖出（基于52周高低点位置）"], index=0)
pb_sell_threshold = 1.0
if sell_mode == "PB估值卖出（PB>阈值）":
    pb_sell_threshold = st.sidebar.number_input("PB大于此值卖出", 0.8, 2.0, 1.0, 0.05)

st.sidebar.subheader("📉 低波动股票处理策略")
low_volatility_strategy = st.sidebar.selectbox("当持仓长期不触发卖出时",
    options=["主动轮动（换入更低估行业）", "关注股息替代", "继续等待经典翻倍"], index=0)
low_volatility_weeks = st.sidebar.number_input("观察周期（周）", 26, 104, 52, step=13)
etf_mode = st.sidebar.checkbox("开启 ETF 推荐（仅影响股票列表）", value=False)

# ========== 颜色含义图例 ==========
st.sidebar.markdown("---")
st.sidebar.markdown("**📌 颜色图例**")
st.sidebar.markdown("- 🔴 红色：卖出信号 / 预警 / 加仓信号")
st.sidebar.markdown("- 🟢 绿色：买入或持有信号 / 估值合理")
st.sidebar.markdown("- 🟡 黄色：观望 / 接近卖出")
st.sidebar.markdown("- 🔵 蓝色：深度破净或特殊策略建议")
st.sidebar.markdown("- ⚪ 白色：中性观望")

module_order = ["股票查询", "我的持仓", "统计概览", "股票列表", "预算推荐", "投资组合整体估值",
                "卖出提醒汇总", "组合再平衡检查", "行业覆盖率统计", "分档建仓模拟",
                "分红参考", "分批建仓参考"]
module_visible = {m: True for m in module_order}
with st.sidebar.expander("🎛️ 自定义布局"):
    for m in module_order:
        module_visible[m] = st.checkbox(m, value=True, key=f"vis_{m}")

with st.sidebar.expander("📖 大数投资理论要点"):
    st.markdown("""
    - **选股标准**：PB<2, PE<20
    - **行业分散**：每个行业选2-3只，不超过总资金的20%
    - **分档建仓**：每跌10-15%加一档（3~5档）
    - **卖出规则核心**：股价从过去一年最低点上涨100%时卖出
    - **仓位管理**：单只股票不超过总资金的5%
    - **低波动处理**：不剔除，采用主动轮动、股息替代或长期持有
    - **剔除范围**：ST、次新(<365天)、停牌、科创、创业板、B股、北交所
    """)

# ========== 基础数据获取函数 ==========
def get_single_stock_52w(code):
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=700)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or df.empty:
            return None, None
        if len(df) > 250:
            df = df.tail(250)
        high = df["最高"].max()
        low = df["最低"].min()
        return round(high, 2), round(low, 2)
    except Exception:
        try:
            info = ak.stock_individual_info_em(symbol=code)
            high_52 = info[info["item"] == "52周最高"]["value"].values[0] if not info[info["item"] == "52周最高"].empty else None
            low_52 = info[info["item"] == "52周最低"]["value"].values[0] if not info[info["item"] == "52周最低"].empty else None
            return float(high_52) if high_52 else None, float(low_52) if low_52 else None
        except:
            return None, None

def save_single_to_cache(code, high, low):
    cache = {}
    if os.path.exists(HIGH_LOW_CACHE_FILE):
        with open(HIGH_LOW_CACHE_FILE, "r") as f:
            cache = json.load(f)
    cache[code] = {"high": high, "low": low, "update_time": datetime.now().isoformat()}
    with open(HIGH_LOW_CACHE_FILE, "w") as f:
        json.dump(cache, f)

def batch_fetch_52w(codes, progress_callback=None, force_overwrite=False):
    if force_overwrite and os.path.exists(HIGH_LOW_CACHE_FILE):
        os.remove(HIGH_LOW_CACHE_FILE)
    cache = {}
    if os.path.exists(HIGH_LOW_CACHE_FILE):
        with open(HIGH_LOW_CACHE_FILE, "r") as f:
            cache = json.load(f)
    need_fetch = codes if force_overwrite else [c for c in codes if c not in cache]
    if not need_fetch:
        if progress_callback:
            progress_callback(len(codes), len(codes))
        return
    total = len(need_fetch)
    completed = 0
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_code = {executor.submit(get_single_stock_52w, code): code for code in need_fetch}
        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                high, low = future.result(timeout=25)
                if high is not None and low is not None:
                    cache[code] = {"high": high, "low": low, "update_time": datetime.now().isoformat()}
                else:
                    cache[code] = {"high": None, "low": None, "update_time": datetime.now().isoformat()}
            except Exception:
                cache[code] = {"high": None, "low": None, "update_time": datetime.now().isoformat()}
            completed += 1
            if progress_callback:
                progress_callback(completed, total)
    with open(HIGH_LOW_CACHE_FILE, "w") as f:
        json.dump(cache, f)

@st.cache_data(ttl=3600, show_spinner=False)
def get_industry_data(force_refresh=False):
    if force_refresh:
        st.cache_data.clear()
        if os.path.exists(INDUSTRY_CACHE_FILE):
            os.remove(INDUSTRY_CACHE_FILE)
    if os.path.exists(INDUSTRY_CACHE_FILE) and not force_refresh:
        try:
            df = pd.read_parquet(INDUSTRY_CACHE_FILE)
            if not df.empty:
                return df
        except:
            pass
    with st.spinner("🔄 正在获取行业数据..."):
        industry_list = ak.stock_board_industry_name_em()
        all_stocks = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_industry = {executor.submit(ak.stock_board_industry_cons_em, row["板块代码"]): row["板块名称"]
                                  for _, row in industry_list.iterrows()}
            for i, future in enumerate(as_completed(future_to_industry)):
                industry_name = future_to_industry[future]
                try:
                    stocks = future.result(timeout=15)
                    for _, stock in stocks.iterrows():
                        all_stocks.append({"代码": stock["代码"], "行业": industry_name})
                except:
                    pass
                progress_bar.progress((i+1)/len(industry_list))
                status_text.text(f"已处理: {i+1}/{len(industry_list)} 个行业")
        progress_bar.empty()
        status_text.empty()
        df_industry = pd.DataFrame(all_stocks).drop_duplicates(subset=["代码"], keep="first")
        df_industry.to_parquet(INDUSTRY_CACHE_FILE)
        return df_industry

@st.cache_data(ttl=3600, show_spinner=False)
def get_realtime_data(force_refresh=False):
    if force_refresh:
        st.cache_data.clear()
        if os.path.exists(REALTIME_CACHE_FILE):
            os.remove(REALTIME_CACHE_FILE)
    if os.path.exists(REALTIME_CACHE_FILE) and not force_refresh:
        try:
            df = pd.read_parquet(REALTIME_CACHE_FILE)
            if not df.empty:
                return df
        except:
            pass
    with st.spinner("📈 正在获取实时行情数据..."):
        df_market = ak.stock_zh_a_spot_em()
        keep_cols = ['代码', '名称', '最新价', '市盈率-动态', '市净率', '涨跌幅', '年初至今涨跌幅']
        df_market = df_market[keep_cols]
        df_market.to_parquet(REALTIME_CACHE_FILE)
        return df_market

def apply_exclusion(df):
    exclude_names = df["名称"].str.contains("ST|\\*ST|退|N|C|U", na=False, regex=True)
    exclude_suspend = (df["最新价"] == 0) | (df["最新价"].isna())
    exclude_new = pd.Series(False, index=df.index)  # 简化，不判断上市天数
    exclude_no_industry = df["行业"].isna()
    code_str = df["代码"].astype(str)
    exclude_b = code_str.str.startswith(('900', '200', '8'))
    keep = ~(exclude_names | exclude_suspend | exclude_new | exclude_no_industry | exclude_b)
    return keep

def load_data(force_refresh):
    df_industry = get_industry_data(force_refresh=force_refresh)
    df_market = get_realtime_data(force_refresh=force_refresh)
    df_raw = pd.merge(df_market, df_industry, on="代码", how="left")
    df_raw["市盈率-动态"] = pd.to_numeric(df_raw["市盈率-动态"], errors="coerce")
    df_raw["市净率"] = pd.to_numeric(df_raw["市净率"], errors="coerce")
    df_raw["涨跌幅"] = pd.to_numeric(df_raw["涨跌幅"], errors="coerce")
    df_raw["年初至今涨跌幅"] = pd.to_numeric(df_raw["年初至今涨跌幅"], errors="coerce")
    code_str = df_raw["代码"].astype(str)
    condition_main = code_str.str.match(r"^(600|601|603|605|000|001|002|003)")
    df_raw = df_raw[condition_main]
    return df_raw

df_raw = load_data(force_refresh=False)
valuation_mask = (df_raw["市盈率-动态"] > 0) & (df_raw["市盈率-动态"] < pe_max) & (df_raw["市净率"] > 0) & (df_raw["市净率"] < pb_max)
df_val = df_raw[valuation_mask].copy()
if not df_val.empty:
    keep_mask = apply_exclusion(df_val)
    df_val = df_val[keep_mask]
if not df_val.empty:
    df_val = df_val.sort_values(["行业", "市盈率-动态"])
    df_val["行业排名"] = df_val.groupby("行业").cumcount() + 1
    df_val = df_val[df_val["行业排名"] <= 3].copy()
else:
    df_val = pd.DataFrame()

all_industries = sorted(df_val["行业"].unique())
with st.sidebar.expander("🚫 剔除不喜欢的行业"):
    excluded_industries = st.multiselect("选择要剔除的行业", all_industries, default=[])
if excluded_industries:
    df_val = df_val[~df_val["行业"].isin(excluded_industries)]

if etf_mode:
    etf_codes = []
    etf_names = []
    for industry in df_val["行业"]:
        code, name = get_etf_for_industry(industry)
        etf_codes.append(code)
        etf_names.append(name)
    df_val["推荐ETF代码"] = etf_codes
    df_val["ETF名称"] = etf_names

# 生成涨幅预警列（修复 KeyError）
df_val["涨幅预警"] = df_val.apply(
    lambda row: f"⚠️ 年内涨幅{row['年初至今涨跌幅']:.1f}% > {year_gain_warning}%" 
    if pd.notna(row['年初至今涨跌幅']) and row['年初至今涨跌幅'] > year_gain_warning else "", axis=1)

def load_52w_cache():
    if os.path.exists(HIGH_LOW_CACHE_FILE):
        with open(HIGH_LOW_CACHE_FILE, "r") as f:
            return json.load(f)
    return {}
cache_52w = load_52w_cache()
df_val["52周最高"] = df_val["代码"].apply(lambda code: cache_52w.get(code, {}).get("high"))
df_val["52周最低"] = df_val["代码"].apply(lambda code: cache_52w.get(code, {}).get("low"))
df_val["52周最低涨幅(%)"] = df_val.apply(
    lambda row: round(((row["最新价"] - row["52周最低"]) / row["52周最低"] * 100), 1) if pd.notna(row["52周最低"]) and row["52周最低"] > 0 else None, axis=1)
df_val["高低点位置(%)"] = df_val.apply(
    lambda row: round((row["最新价"] - row["52周最低"]) / (row["52周最高"] - row["52周最低"]) * 100, 1) if pd.notna(row["52周最高"]) and pd.notna(row["52周最低"]) and row["52周最高"] > row["52周最低"] else None, axis=1)

# ========== 建议函数（返回项目符号格式文本） ==========
def get_classic_advice(low, price):
    if low and low > 0 and price:
        rise = (price - low) / low * 100
        if rise >= 100:
            return "🔴 卖出（已翻倍）"
        elif rise >= 80:
            return "🟡 考虑卖出（接近翻倍）"
        else:
            return "🟢 可买入/持有"
    else:
        return "⚪ 无足够数据"

def get_pb_advice(pb):
    if pd.notna(pb):
        if pb > pb_sell_threshold:
            return f"🔴 卖出（PB>{pb_sell_threshold}）"
        elif pb > pb_sell_threshold - 0.2:
            return f"🟡 接近卖出（PB={pb:.2f}）"
        else:
            return "🟢 估值合理，可持有"
    else:
        return "⚪ 无PB数据"

def get_position_advice(high, low, price):
    if high and low and high > low and price:
        pos = (price - low) / (high - low) * 100
        if pos <= buy_position_threshold:
            return f"🟢 买入推荐（位置{pos:.0f}%）"
        elif pos >= (100 - sell_position_threshold):
            return f"🔴 卖出推荐（位置{pos:.0f}%）"
        else:
            return f"⚪ 持有观望（位置{pos:.0f}%）"
    else:
        return "⚪ 无52周数据"

def get_all_advice_bullets(row):
    price = row.get("最新价")
    low = row.get("52周最低")
    high = row.get("52周最高")
    pb = row.get("市净率")
    lines = []
    lines.append(f"- 📌 核心规则：{get_classic_advice(low, price)}")
    lines.append(f"- 🟢 PB估值：{get_pb_advice(pb)}")
    lines.append(f"- 🟢 位置比例：{get_position_advice(high, low, price)}")
    # 加仓信号（若有）
    if "买入" in lines[0] and price and low and (price - low)/low*100 < 10:
        lines.append("- 📉 建议：股价接近低点，可考虑加仓")
    return "\n".join(lines)

df_val["操作建议"] = df_val.apply(get_all_advice_bullets, axis=1)

# ========== 预算推荐 ==========
if 'budget_recommend' not in st.session_state:
    st.session_state.budget_recommend = None

def compute_budget_recommend_with_limit(budget_val, df_val_, max_weight_pct):
    if df_val_.empty:
        return []
    df_top1 = df_val_[df_val_["行业排名"] == 1].copy()
    df_top1 = df_top1.sort_values("市盈率-动态")
    items = []
    total = 0
    max_per_stock = budget_val * (max_weight_pct / 100.0)
    for _, row in df_top1.iterrows():
        price = row["最新价"]
        unit_cost = price * 100
        if unit_cost > max_per_stock:
            continue
        if total + unit_cost <= budget_val:
            items.append({
                "代码": row["代码"], "名称": row["名称"], "最新价": price,
                "市盈率-动态": row["市盈率-动态"], "市净率": row["市净率"],
                "涨跌幅": row["涨跌幅"], "年初至今涨跌幅": row["年初至今涨跌幅"],
                "行业": row["行业"], "行业排名": row["行业排名"],
                "52周最低": row["52周最低"], "52周最高": row["52周最高"],
                "52周最低涨幅(%)": row["52周最低涨幅(%)"], "高低点位置(%)": row["高低点位置(%)"],
                "涨幅预警": row["涨幅预警"], "操作建议": row["操作建议"],
                "买入成本": unit_cost, "数量": 100,
            })
            total += unit_cost
        else:
            break
    return items

def render_budget_recommend():
    st.subheader("💰 预算推荐（覆盖行业广度优先 + 仓位限制）")
    col1, col2 = st.columns([3, 1])
    with col1:
        budget = st.number_input("我的总预算（元）", min_value=500, max_value=1000000, value=10000, step=1000, key="budget_main")
    with col2:
        refresh_btn = st.button("🔄 刷新推荐", key="refresh_budget_main")
    if refresh_btn:
        st.session_state.budget_recommend = compute_budget_recommend_with_limit(budget, df_val, max_single_weight)
        st.rerun()
    if st.session_state.budget_recommend is None:
        st.session_state.budget_recommend = compute_budget_recommend_with_limit(budget, df_val, max_single_weight)
    items = st.session_state.budget_recommend
    if items:
        rec_df = pd.DataFrame(items)
        display_cols = ["代码", "名称", "最新价", "市盈率-动态", "市净率", "行业", "行业排名",
                        "52周最低", "52周最高", "高低点位置(%)", "操作建议", "买入成本"]
        rec_df = rec_df[display_cols]
        for col in ["52周最高", "52周最低"]:
            rec_df[col] = rec_df[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "暂无")
        rec_df["高低点位置(%)"] = rec_df["高低点位置(%)"].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "暂无")
        rec_df["最新价"] = rec_df["最新价"].apply(lambda x: f"{x:.2f}")
        rec_df["市盈率-动态"] = rec_df["市盈率-动态"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "暂无")
        rec_df["市净率"] = rec_df["市净率"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "暂无")
        rec_df["买入成本"] = rec_df["买入成本"].apply(lambda x: f"{x:.0f} 元")
        st.dataframe(rec_df, use_container_width=True)
        total_cost = sum(item["买入成本"] for item in items)
        remaining = budget - total_cost
        st.success(f"总成本: {total_cost} 元 | 覆盖行业数: {len(items)} | 剩余预算: {remaining} 元 | 单只最大仓位: {max_single_weight}%（约 {budget * max_single_weight / 100:.0f} 元）")
        csv_rec = rec_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ 导出推荐清单", csv_rec, f"推荐清单_{budget}元.csv", "text/csv")
    else:
        st.warning(f"预算 {budget} 元不足以购买任何一手股票，或所有候选均超过仓位限制。")

# ========== 股票列表渲染（使用项目符号操作建议） ==========
def render_stock_list(refresh_key):
    st.subheader("📋 符合条件的股票列表")
    col1, col2 = st.columns([6,1])
    if col2.button("🔄 刷新列表", key=refresh_key):
        st.cache_data.clear()
        for f in [REALTIME_CACHE_FILE, INDUSTRY_CACHE_FILE]:
            if os.path.exists(f): os.remove(f)
        st.rerun()
    if df_val.empty:
        st.warning("暂无符合条件的股票")
        return
    display_cols = ["代码","名称","最新价","市盈率-动态","市净率","行业","行业排名","52周最低","52周最高","高低点位置(%)","操作建议"]
    df_display = df_val[display_cols].copy()
    for col in ["52周最高","52周最低"]:
        df_display[col] = df_display[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "暂无")
    df_display["高低点位置(%)"] = df_display["高低点位置(%)"].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "暂无")
    df_display["最新价"] = df_display["最新价"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "暂无")
    df_display["市盈率-动态"] = df_display["市盈率-动态"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "暂无")
    df_display["市净率"] = df_display["市净率"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "暂无")
    # 操作建议已包含项目符号，直接显示文本
    def style_rows(row):
        rank = row['行业排名']
        if rank == 1:
            return ['background-color: #ffcccc'] * len(row)
        elif rank == 2:
            return ['background-color: #ffe6cc'] * len(row)
        else:
            return [''] * len(row)
    styled_df = df_display.style.apply(style_rows, axis=1)
    st.dataframe(styled_df, use_container_width=True)
    st.info("💡 同行业第一名红色背景，第二名橙色背景。操作建议以项目符号列出各项规则。")

# ========== 个股综合诊断 ==========
def render_stock_query():
    st.subheader("🔍 个股综合诊断")
    query_code = st.text_input("输入股票代码（6位）", key="query_code_input")
    if st.button("开始诊断", key="query_btn"):
        if not query_code:
            st.error("请输入股票代码")
            return
        stock_info = df_raw[df_raw["代码"] == query_code]
        if not stock_info.empty:
            stock_name = stock_info.iloc[0]["名称"]
            stock_industry = stock_info.iloc[0]["行业"] if "行业" in stock_info.columns else "未知"
            current_price = stock_info.iloc[0]["最新价"]
            pe = stock_info.iloc[0]["市盈率-动态"]
            pb = stock_info.iloc[0]["市净率"]
            change_pct = stock_info.iloc[0]["涨跌幅"]
            ytd = stock_info.iloc[0]["年初至今涨跌幅"]
            has_price = True
        else:
            try:
                info = ak.stock_individual_info_em(symbol=query_code)
                stock_name = info[info["item"] == "股票简称"]["value"].values[0] if not info[info["item"] == "股票简称"].empty else query_code
            except:
                stock_name = query_code
            industry_df = get_industry_data()
            industry_df = industry_df[industry_df["代码"] == query_code] if not industry_df.empty else pd.DataFrame()
            stock_industry = industry_df.iloc[0]["行业"] if not industry_df.empty else "待补充"
            current_price = None
            pe = None
            pb = None
            change_pct = None
            ytd = None
            has_price = False
            st.info("该股票可能不在主板或暂未上市，数据不完整。")
        cache = load_52w_cache()
        if query_code in cache:
            high = cache[query_code].get("high")
            low = cache[query_code].get("low")
        else:
            with st.spinner(f"正在获取{stock_name}的52周数据..."):
                high, low = get_single_stock_52w(query_code)
                if high is not None and low is not None:
                    save_single_to_cache(query_code, high, low)
                else:
                    high = low = None
        st.markdown(f"## 📌 {stock_name}（{query_code}）综合诊断")
        col1, col2, col3 = st.columns(3)
        col1.metric("行业", stock_industry)
        col2.metric("现价", f"{current_price:.2f}" if current_price else "暂无")
        col3.metric("年内涨跌幅", f"{ytd:.2f}%" if ytd is not None else "暂无", delta=f"{change_pct:.2f}%" if change_pct is not None else None)
        st.markdown("### 估值指标")
        col_a, col_b = st.columns(2)
        pe_str = f"{pe:.2f}" if pd.notna(pe) else "暂无"
        pb_str = f"{pb:.2f}" if pd.notna(pb) else "暂无"
        col_a.metric("市盈率 PE (动态)", pe_str, help="大数投资建议 PE<20")
        col_b.metric("市净率 PB", pb_str, help="大数投资建议 PB<2")
        st.markdown("### 📈 52周位置与卖出规则")
        if high and low:
            st.write(f"**52周最高**: {high:.2f}  |  **52周最低**: {low:.2f}")
            if current_price:
                rise_from_low = (current_price - low) / low * 100
                pos = (current_price - low) / (high - low) * 100
                st.metric("距52周最低涨幅", f"{rise_from_low:.1f}%", delta_color="inverse")
                st.metric("高低点位置", f"{pos:.1f}%")
                classic = get_classic_advice(low, current_price)
                st.markdown(f"<div class='main-advice'>📌 核心卖出规则（经典翻倍）：{classic}</div>", unsafe_allow_html=True)
            else:
                st.warning("无实时价格，无法计算位置")
        else:
            st.warning("无52周数据，请点击「批量获取52周数据」后重试")
        if has_price and pb is not None:
            st.markdown("#### 辅助卖出参考")
            pb_adv = get_pb_advice(pb)
            pos_adv = get_position_advice(high, low, current_price) if high and low and current_price else "无足够数据"
            st.write(f"- **PB估值规则**：{pb_adv}")
            st.write(f"- **位置比例规则**：{pos_adv}")
        # 组合适配性分析（基于预算推荐）
        items = st.session_state.budget_recommend
        if items is None or len(items) == 0:
            st.info("请先在「预算推荐」模块生成模拟组合，以便分析行业占比和仓位限制。")
        else:
            df_port = pd.DataFrame(items)
            industry_count = len(df_port[df_port["行业"] == stock_industry]) if stock_industry != "待补充" else 0
            industry_total_cost = df_port[df_port["行业"] == stock_industry]["买入成本"].sum() if stock_industry != "待补充" else 0
            total_port_cost = df_port["买入成本"].sum()
            industry_ratio = (industry_total_cost / total_port_cost * 100) if total_port_cost > 0 else 0
            single_cost = current_price * 100 if current_price else 0
            budget = st.session_state.get("budget_main", 10000)
            max_per_stock = budget * (max_single_weight / 100.0)
            remaining_budget = budget - total_port_cost
            st.write(f"- **当前组合中「{stock_industry}」行业已有 {industry_count} 只股票，市值占比 {industry_ratio:.1f}%**")
            if industry_ratio > 20:
                st.warning("⚠️ 该行业占比已超过大数投资建议的20%，不建议再增加该行业股票。")
            else:
                st.success("✅ 行业占比仍在20%以内，可适当配置。")
            if single_cost > max_per_stock:
                st.warning(f"⚠️ 买入一手（{single_cost:.0f}元）将超过单只股票最大仓位限制（{max_per_stock:.0f}元，即总预算的{max_single_weight}%）。不建议买入。")
            elif remaining_budget < single_cost:
                st.warning(f"⚠️ 买入一手需 {single_cost:.0f} 元，但预算剩余 {remaining_budget:.0f} 元，预算不足。")
            else:
                st.success(f"✅ 买入一手需 {single_cost:.0f} 元，符合仓位限制且预算充足。")
        # 高股息低波动专用建议
        if pb is not None and pe is not None and pb < 1.5 and pe < 15:
            st.markdown("### 💰 高股息低波动专用建议")
            div_adv = get_dividend_lowvol_advice({"市净率": pb, "最新价": current_price, "52周最低": low, "52周最高": high})
            st.info(div_adv)
        if current_price:
            st.markdown("### 📦 分档建仓参考（每下跌10%加仓一次，股数递增50%）")
            price_tier = current_price
            shares_tier = 100
            tier_data = []
            for i in range(1, 4):
                tier_data.append({"档位": i, "买入价": round(price_tier, 2), "股数": shares_tier, "投资额": round(price_tier * shares_tier, 2)})
                price_tier = price_tier * 0.9
                shares_tier = int(shares_tier * 1.5)
            df_tier = pd.DataFrame(tier_data)
            st.dataframe(df_tier, use_container_width=True)
            total_invest = df_tier["投资额"].sum()
            total_shares = df_tier["股数"].sum()
            avg_cost = total_invest / total_shares if total_shares>0 else 0
            st.caption(f"总投入 {total_invest:.0f} 元 | 平均成本 {avg_cost:.2f} 元 | 翻倍卖出价 {avg_cost*2:.2f} 元")
        # 诊断报告
        pe_str_report = f"{pe:.2f}" if pd.notna(pe) else "暂无"
        pb_str_report = f"{pb:.2f}" if pd.notna(pb) else "暂无"
        high_str = f"{high:.2f}" if high else "暂无"
        low_str = f"{low:.2f}" if low else "暂无"
        classic_str = get_classic_advice(low, current_price) if current_price else "无数据"
        pb_adv_str = get_pb_advice(pb) if has_price else "无数据"
        pos_adv_str = get_position_advice(high, low, current_price) if has_price and high and low else "无数据"
        report = f"""股票代码：{query_code}
名称：{stock_name}
行业：{stock_industry}
现价：{current_price if current_price else '暂无'}
PE：{pe_str_report} (建议<20)
PB：{pb_str_report} (建议<2)
52周最高：{high_str}  52周最低：{low_str}
距低点涨幅：{rise_from_low:.1f}% (若≥100%则卖出)
核心卖出建议：{classic_str}
辅助PB建议：{pb_adv_str}
辅助位置建议：{pos_adv_str}"""
        st.download_button("📋 复制诊断报告", report, file_name=f"{query_code}_诊断报告.txt", mime="text/plain")

def get_dividend_lowvol_advice(row):
    pb = row.get("市净率")
    price = row.get("最新价")
    low = row.get("52周最低")
    high = row.get("52周最高")
    at_low = False
    if low and high and price and (high - low) != 0:
        pos = (price - low) / (high - low) * 100
        if pos <= 20:
            at_low = True
    if pd.notna(pb) and pb < 0.8:
        return "🔵 深度破净 + 高股息潜力 → 极佳安全垫，适合长期持有作为底仓，股息再投资。"
    elif pd.notna(pb) and pb < 1.0:
        return "🟢 股价处于52周低位附近 + PB<1 → 低估值股息配置窗口，可分批建仓。" if at_low else "🟢 PB<1，股息保障较强，无需拘泥于翻倍卖出，关注分红持续性即可。"
    elif pd.notna(pb) and pb < 1.5:
        return "🟡 估值偏低且接近低点，可作为债券替代品，长期持有获取股息。" if at_low else "⚪ 低波动高股息特征，适合稳健型配置，不必频繁交易。"
    else:
        return "📌 大数投资原则：低波动股票优先作为安全边际来源，长期持有，不轻易卖出。可关注分红稳定性。"

# ========== 我的持仓（含删除按钮，综合建议使用项目符号） ==========
def render_my_portfolio():
    st.subheader("📋 我的持仓")
    with st.expander("➕ 添加持仓", expanded=False):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            code_input = st.text_input("股票代码", key="port_code")
        with col2:
            buy_price = st.number_input("买入价（元）", min_value=0.01, step=0.01, key="port_price")
        with col3:
            shares = st.number_input("股数", min_value=100, step=100, value=100, key="port_shares")
        with col4:
            buy_date = st.date_input("买入日期", value=datetime.now().date(), key="port_date")
        if st.button("添加到持仓", key="add_portfolio"):
            if not code_input:
                st.error("请输入股票代码")
            else:
                stock_info = df_raw[df_raw["代码"] == code_input]
                if not stock_info.empty:
                    name = stock_info.iloc[0]["名称"]
                    industry = stock_info.iloc[0]["行业"] if "行业" in stock_info.columns else "未知"
                else:
                    try:
                        info = ak.stock_individual_info_em(symbol=code_input)
                        name = info[info["item"] == "股票简称"]["value"].values[0] if not info[info["item"] == "股票简称"].empty else code_input
                    except:
                        name = code_input
                    industry_df = get_industry_data()
                    industry_df = industry_df[industry_df["代码"] == code_input] if not industry_df.empty else pd.DataFrame()
                    industry = industry_df.iloc[0]["行业"] if not industry_df.empty else "待补充"
                new_row = pd.DataFrame([{'代码': code_input, '名称': name, '行业': industry,
                                         '买入价': buy_price, '股数': shares,
                                         '买入日期': buy_date.strftime("%Y-%m-%d"), '备注': ''}])
                st.session_state.portfolio = pd.concat([st.session_state.portfolio, new_row], ignore_index=True)
                save_portfolio(st.session_state.portfolio)
                st.success(f"已添加 {name}({code_input}) 到持仓")
                st.rerun()
    if st.session_state.portfolio.empty:
        st.info("暂无持仓，请添加上方添加。")
        return

    # 表头
    headers = ["代码", "名称", "行业", "成本价", "股数", "成本总额", "现价", "市值", "盈亏", "PE", "PB", "距低点涨幅", "综合操作建议", "删除"]
    col_widths = [0.8, 1.2, 1, 0.8, 0.6, 1, 0.8, 1, 1.2, 0.8, 0.8, 1, 2.2, 0.8]
    cols = st.columns(col_widths)
    for col, header in zip(cols, headers):
        col.write(f"**{header}**")

    for idx, row in st.session_state.portfolio.iterrows():
        code = row['代码']
        stock_info = df_raw[df_raw["代码"] == code]
        if not stock_info.empty:
            current_price = stock_info.iloc[0]["最新价"]
            pe = stock_info.iloc[0]["市盈率-动态"]
            pb = stock_info.iloc[0]["市净率"]
        else:
            current_price = None
            pe = None
            pb = None
        cache = load_52w_cache()
        if code in cache:
            high = cache[code].get('high')
            low = cache[code].get('low')
        else:
            high = low = None

        buy_price = row['买入价']
        shares = row['股数']
        cost = buy_price * shares
        if current_price:
            market_val = current_price * shares
            profit = market_val - cost
            profit_pct = (profit / cost) * 100
        else:
            market_val = None
            profit = None
            profit_pct = None

        # 生成综合建议（项目符号）
        advice_lines = []
        if low and current_price:
            advice_lines.append(f"- 📌 核心规则：{get_classic_advice(low, current_price)}")
            advice_lines.append(f"- 🟢 PB估值：{get_pb_advice(pb) if pb is not None else '无PB数据'}")
            advice_lines.append(f"- 🟢 位置比例：{get_position_advice(high, low, current_price) if high and low else '无位置数据'}")
        else:
            advice_lines.append("- ⚪ 数据不足，无法给出建议")
        # 加仓信号
        if current_price and buy_price:
            drop_pct = (buy_price - current_price) / buy_price * 100
            if drop_pct >= alert_drop:
                advice_lines.append(f"- 🔴 加仓信号：已跌 {drop_pct:.0f}%，建议加仓")
        advice_text = "\n".join(advice_lines)

        # 格式化数值
        cost_str = f"{cost:.0f}"
        market_val_str = f"{market_val:.0f}" if market_val else "暂无"
        profit_str = f"{profit:.0f} ({profit_pct:.1f}%)" if profit is not None else "暂无"
        pe_str = f"{pe:.2f}" if pd.notna(pe) else "暂无"
        pb_str = f"{pb:.2f}" if pd.notna(pb) else "暂无"
        rise_from_low = ((current_price - low) / low * 100) if low and current_price else None
        rise_str = f"{rise_from_low:.1f}%" if rise_from_low is not None else "暂无"
        price_str = f"{current_price:.2f}" if current_price else "暂无"

        cols_data = st.columns(col_widths)
        cols_data[0].write(code)
        cols_data[1].write(row['名称'])
        cols_data[2].write(row['行业'])
        cols_data[3].write(f"{buy_price:.2f}")
        cols_data[4].write(shares)
        cols_data[5].write(cost_str)
        cols_data[6].write(price_str)
        cols_data[7].write(market_val_str)
        cols_data[8].write(profit_str)
        cols_data[9].write(pe_str)
        cols_data[10].write(pb_str)
        cols_data[11].write(rise_str)
        cols_data[12].markdown(advice_text)  # 使用 markdown 支持换行
        if cols_data[13].button("❌", key=f"del_{idx}"):
            st.session_state.portfolio.drop(idx, inplace=True)
            save_portfolio(st.session_state.portfolio)
            st.rerun()

    st.markdown("---")
    if st.button("📥 导出持仓数据到 CSV"):
        csv = st.session_state.portfolio.to_csv(index=False).encode("utf-8-sig")
        st.download_button("下载 CSV", csv, "portfolio.csv", "text/csv")

    st.subheader("🔔 持仓卖出提醒（基于经典翻倍规则）")
    alerts = []
    for idx, row in st.session_state.portfolio.iterrows():
        code = row['代码']
        cache = load_52w_cache()
        if code in cache:
            low = cache[code].get('low')
            stock_info = df_raw[df_raw["代码"] == code]
            if not stock_info.empty:
                current_price = stock_info.iloc[0]["最新价"]
                if low and current_price:
                    classic = get_classic_advice(low, current_price)
                    if "卖出" in classic:
                        pb = stock_info.iloc[0]["市净率"] if "市净率" in stock_info.columns else None
                        high = cache[code].get('high')
                        pb_adv = get_pb_advice(pb) if pb is not None else "无PB数据"
                        pos_adv = get_position_advice(high, low, current_price) if high else "无位置数据"
                        alerts.append({"名称": row['名称'], "代码": code, "核心卖出信号": classic, "PB建议": pb_adv, "位置建议": pos_adv})
    if alerts:
        st.dataframe(pd.DataFrame(alerts), use_container_width=True)
        st.warning("以上持仓触发核心卖出规则（翻倍），建议果断卖出，轮入其他低估行业股票。综合参考各项建议后决策。")
    else:
        st.success("当前持仓无卖出信号，可继续持有。")

# ========== 其他模块（简化，但已包含必要组件） ==========
def render_statistics():
    st.subheader("📊 统计概览")
    col1, col2, col3 = st.columns(3)
    col1.metric("候选股票总数", len(df_val))
    col2.metric("覆盖行业数", df_val["行业"].nunique())
    col3.metric("更新时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    st.markdown("---")
    col_btn1, col_btn2 = st.columns(2)
    if col_btn1.button("🔄 强制刷新所有数据", use_container_width=True):
        st.cache_data.clear()
        for f in [REALTIME_CACHE_FILE, INDUSTRY_CACHE_FILE]:
            if os.path.exists(f): os.remove(f)
        st.rerun()
    if col_btn2.button("📥 批量获取52周数据（覆盖缓存）", use_container_width=True):
        if not df_val.empty:
            codes = df_val["代码"].tolist()
            batch_fetch_52w(codes, force_overwrite=True)
            st.success("完成")
            st.rerun()
        else:
            st.warning("无候选股")

def render_portfolio_valuation():
    st.subheader("📊 投资组合整体估值")
    items = st.session_state.budget_recommend
    if not items:
        st.info("请先在「预算推荐」中生成模拟组合。")
        return
    df_port = pd.DataFrame(items)
    total_cost = df_port["买入成本"].sum()
    current_values = []
    pe_vals, pb_vals, div_vals = [], [], []
    for _, row in df_port.iterrows():
        code = row["代码"]
        stock_info = df_raw[df_raw["代码"] == code]
        if not stock_info.empty:
            cp = stock_info.iloc[0]["最新价"]
            pe = stock_info.iloc[0]["市盈率-动态"]
            pb = stock_info.iloc[0]["市净率"]
        else:
            cp = row["最新价"]
            pe = row["市盈率-动态"]
            pb = row["市净率"]
        current_val = cp * 100
        current_values.append(current_val)
        if pd.notna(pe) and pe>0: pe_vals.append(pe)
        if pd.notna(pb) and pb>0: pb_vals.append(pb)
        if pd.notna(pe) and pe>0: div_vals.append(0.3/pe*100)
    total_market = sum(current_values)
    weights = [v/total_market for v in current_values] if total_market>0 else []
    w_pe = np.average(pe_vals, weights=weights[:len(pe_vals)]) if pe_vals else None
    w_pb = np.average(pb_vals, weights=weights[:len(pb_vals)]) if pb_vals else None
    avg_div = np.mean(div_vals) if div_vals else None
    col1,col2,col3,col4 = st.columns(4)
    col1.metric("总投入成本", f"{total_cost:.0f} 元")
    col2.metric("当前总市值", f"{total_market:.0f} 元", delta=f"{total_market-total_cost:.0f}")
    col3.metric("加权平均PE", f"{w_pe:.2f}" if w_pe else "暂无")
    col4.metric("加权平均PB", f"{w_pb:.2f}" if w_pb else "暂无")
    if avg_div:
        st.metric("预估平均股息率", f"{avg_div:.2f}%", help="假设分红率30%")

def render_sell_alerts():
    st.subheader("🔔 卖出提醒汇总（基于经典翻倍）")
    items = st.session_state.budget_recommend
    if not items:
        st.info("请先在「预算推荐」中生成模拟组合。")
        return
    alerts = []
    for item in items:
        classic = get_classic_advice(item["52周最低"], item["最新价"])
        if "卖出" in classic:
            alerts.append({"名称":item["名称"], "代码":item["代码"], "核心卖出信号":classic})
    if alerts:
        st.dataframe(pd.DataFrame(alerts), use_container_width=True)
        st.warning("以上股票触发核心卖出规则，建议卖出。")
    else:
        st.success("目前无卖出信号。")

def render_rebalance_check():
    st.subheader("🔄 组合再平衡检查")
    items = st.session_state.budget_recommend
    if not items:
        st.info("请先在「预算推荐」中生成模拟组合。")
        return
    data = []
    for item in items:
        classic = get_classic_advice(item["52周最低"], item["最新价"])
        if "卖出" in classic:
            action = "🔴 建议卖出"
            industry_stocks = df_val[df_val["行业"] == item["行业"]]
            swap = industry_stocks[industry_stocks["行业排名"] > 1].head(2)
            swap_text = " -> 换仓至：" + ", ".join([f"{r['名称']}({r['代码']})" for _, r in swap.iterrows()]) if not swap.empty else "（无替代）"
        else:
            action = "🟢 继续持有"
            swap_text = ""
        rise = (item["最新价"] - item["52周最低"])/item["52周最低"]*100 if item["52周最低"] else 0
        data.append({"名称":item["名称"],"代码":item["代码"],"行业":item["行业"],"当前价":f"{item['最新价']:.2f}",
                     "距低点涨幅":f"{rise:.1f}%","建议":action,"轮动目标":swap_text})
    st.dataframe(pd.DataFrame(data), use_container_width=True)

def render_industry_coverage():
    st.subheader("🏭 行业覆盖率统计")
    items = st.session_state.budget_recommend
    if not items:
        st.info("请先在「预算推荐」中生成模拟组合。")
        return
    df_port = pd.DataFrame(items)
    current_values = []
    for _, row in df_port.iterrows():
        code = row["代码"]
        stock_info = df_raw[df_raw["代码"] == code]
        cp = stock_info.iloc[0]["最新价"] if not stock_info.empty else row["最新价"]
        current_values.append(cp*100)
    df_port["当前市值"] = current_values
    total = df_port["当前市值"].sum()
    stats = df_port.groupby("行业").agg(股票数量=("代码","count"),总市值=("当前市值","sum")).reset_index()
    stats["市值占比(%)"] = (stats["总市值"]/total*100).round(2)
    stats = stats.sort_values("市值占比(%)", ascending=False)
    st.dataframe(stats, use_container_width=True)
    if not stats.empty and stats.iloc[0]["市值占比(%)"] > 20:
        st.warning(f"⚠️ 行业「{stats.iloc[0]['行业']}」占比超过20%")
    else:
        st.success("行业分散度良好")

def render_batch_build():
    st.subheader("📝 分批建仓模拟")
    stock_code = st.text_input("股票代码", key="batch_code")
    first_price = st.number_input("第一档买入价（元）", 0.01, 1000.0, 10.0, key="first_price")
    shares_first = st.number_input("第一档股数（手）", 1, 100, 1, step=1) * 100
    drop_step = st.slider("每档下跌幅度（%）", 5, 20, 10, step=1)
    if st.button("生成加仓计划", key="gen_batch"):
        if not stock_code:
            st.error("请输入股票代码")
        else:
            levels = []
            price = first_price
            shares = shares_first
            for i in range(1,5):
                levels.append({"档位":i,"加仓价":round(price,2),"加仓股数":shares,"投资金额":round(price*shares,2)})
                price *= (1 - drop_step/100)
                shares = int(shares * 1.5)
            df_levels = pd.DataFrame(levels)
            st.dataframe(df_levels)
            total_invest = df_levels["投资金额"].sum()
            total_shares = df_levels["加仓股数"].sum()
            avg_cost = total_invest / total_shares if total_shares>0 else 0
            st.success(f"总投入 {total_invest:.2f} 元 | 平均成本 {avg_cost:.2f} 元 | 翻倍卖出价 {avg_cost*2:.2f} 元")

def render_dividend_ref(refresh_key):
    st.subheader("💰 高股息低波动策略参考")
    col1, col2 = st.columns([6,1])
    if col2.button("🔄 刷新参考", key=refresh_key):
        st.cache_data.clear()
        for f in [REALTIME_CACHE_FILE, INDUSTRY_CACHE_FILE]:
            if os.path.exists(f): os.remove(f)
        st.rerun()
    if df_val.empty:
        st.info("暂无数据")
        return
    low_vol = df_val[(df_val["市净率"] < 1.5) & (df_val["市盈率-动态"] < 15)].copy()
    if low_vol.empty:
        st.info("当前无低估值候选股具备高股息潜力")
        return
    low_vol["高股息低波动专用建议"] = low_vol.apply(get_dividend_lowvol_advice, axis=1)
    display = low_vol[["代码","名称","最新价","市盈率-动态","市净率","高低点位置(%)","高股息低波动专用建议"]]
    st.dataframe(display, use_container_width=True)

def render_batch_ref():
    with st.expander("📌 分批建仓参考"):
        st.markdown(f"""
        - 分 **3~5 份**，第一份当前价买入，随后每下跌 {alert_drop}% 加一份  
        - **卖出规则**：经典翻倍（核心）、PB估值、位置比例
        - **低波动处理**：{low_volatility_strategy}（观察期{low_volatility_weeks}周）
        """)
        if not df_val.empty:
            df_top1 = df_val[df_val["行业排名"] == 1].head(5)
            if not df_top1.empty:
                st.dataframe(df_top1[["名称","最新价","52周最低","高低点位置(%)"]], use_container_width=True)

# ========== 辅助函数 ETF 映射 ==========
def get_etf_for_industry(industry_name):
    # 简单映射，如需完整请补全
    return "", "暂无对应ETF"

# ========== 主界面渲染 ==========
if module_visible["股票查询"]:
    render_stock_query()
if module_visible["我的持仓"]:
    render_my_portfolio()
if module_visible["统计概览"]:
    render_statistics()
if module_visible["股票列表"]:
    render_stock_list(refresh_key="refresh_stock_list")
if module_visible["预算推荐"]:
    render_budget_recommend()
if module_visible["投资组合整体估值"]:
    render_portfolio_valuation()
if module_visible["卖出提醒汇总"]:
    render_sell_alerts()
if module_visible["组合再平衡检查"]:
    render_rebalance_check()
if module_visible["行业覆盖率统计"]:
    render_industry_coverage()
if module_visible["分档建仓模拟"]:
    render_batch_build()
if module_visible["分红参考"]:
    render_dividend_ref(refresh_key="refresh_dividend")
if module_visible["分批建仓参考"]:
    render_batch_ref()

st.caption("⚠️ 本工具遵循大数投资原则，实时行情每小时缓存一次，52周数据手动获取。点击刷新按钮可强制更新。")