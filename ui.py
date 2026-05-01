# -*- coding: utf-8 -*-
import math
from pathlib import Path

import streamlit as st

# 设置Streamlit主题 - 必须是第一个st命令
st.set_page_config(layout="wide", page_title="AI代码审查平台", page_icon="🤖", initial_sidebar_state="expanded")

import datetime
import os
import hashlib
import hmac
import base64
import time
import html
import pandas as pd
from dotenv import load_dotenv
import matplotlib.pyplot as plt
import matplotlib as mpl
import matplotlib.font_manager as fm
import streamlit as st

from biz.service.review_service import ReviewService
from matplotlib.ticker import MaxNLocator
from streamlit_cookies_manager import CookieManager

load_dotenv("conf/.env")

HIGH_SCORE_COLOR = "#8CCF90"
LOW_SCORE_COLOR = "#E6E944"
SCORE_EMOJI_PARAM = os.getenv("SCORE_EMOJI_DISPLAY", os.getenv("SCORE_EMOJI", "1"))
REVIEW_SCORE_HIGH_THRESHOLD = int(os.getenv("REVIEW_SCORE_HIGH_THRESHOLD", "90"))
REVIEW_SCORE_LOW_THRESHOLD = int(os.getenv("REVIEW_SCORE_LOW_THRESHOLD", "60"))


def set_global_font():
    """设置全局字体，如果字体文件不存在则忽略并使用默认字��"""
    font_path = "fonts/SourceHanSansCN-Regular.otf"
    if Path(font_path).exists():
        try:
            fm.fontManager.addfont(font_path)
            mpl.rcParams["font.family"] = "Source Han Sans CN"
        except Exception as e:
            st.warning(f"字体加载失败，使用默认字体。错误信息：{e}")
    else:
        st.warning(f"字体文件未找到：{font_path}，将使用默认字体。")

    mpl.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题


# 在项目启动时调用
set_global_font()

# 从环境变量中读取用户名和密码
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
USER_CREDENTIALS = {
    DASHBOARD_USER: DASHBOARD_PASSWORD
}

# 用于生成和验证token的密钥
SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "fac8cf149bdd616c07c1a675c4571ccacc40d7f7fe16914cfe0f9f9d966bb773")

# 初始化cookie管理器
cookies = CookieManager()


def generate_token(username):
    """生成包含时间戳的认证token"""
    timestamp = str(int(time.time()))
    message = f"{username}:{timestamp}"

    # 使用HMAC-SHA256生成签名
    signature = hmac.new(
        SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256
    ).digest()

    # 将消息和签名编码为base64
    token = base64.b64encode(f"{message}:{base64.b64encode(signature).decode()}".encode()).decode()
    return token


def verify_token(token):
    """验证token的有效性并提取用户名"""
    try:
        # 解码token
        decoded = base64.b64decode(token.encode()).decode()
        message, signature = decoded.rsplit(":", 1)
        username, timestamp = message.split(":", 1)

        # 验证签名
        expected_signature = hmac.new(
            SECRET_KEY.encode(),
            message.encode(),
            hashlib.sha256
        ).digest()

        actual_signature = base64.b64decode(signature)

        if not hmac.compare_digest(expected_signature, actual_signature):
            return None

        # 检查token是否过期（30天）
        if int(time.time()) - int(timestamp) > 30 * 24 * 60 * 60:
            return None

        return username
    except:
        return None


# 检查登录状态
def check_login_status():
    if not cookies.ready():
        st.stop()

    if 'login_status' not in st.session_state:
        st.session_state['login_status'] = False

    # 尝试从cookie获取token
    auth_token = cookies.get('auth_token')
    if auth_token:
        username = verify_token(auth_token)
        if username and username in USER_CREDENTIALS:
            st.session_state['login_status'] = True
            st.session_state['username'] = username
            st.session_state['saved_username'] = username

    return st.session_state['login_status']


# 设置登录状态
def set_login_status(username, remember):
    st.session_state['login_status'] = True
    st.session_state['username'] = username
    st.session_state['saved_username'] = username if remember else ''

    if remember:
        # 生成并保存token到cookie
        auth_token = generate_token(username)
        cookies['auth_token'] = auth_token
    else:
        # 如果不记住登录状态，清除cookie
        if 'auth_token' in cookies:
            del cookies['auth_token']
    cookies.save()


# 获取保存的用户名
def get_saved_credentials():
    auth_token = cookies.get('auth_token')
    if auth_token:
        username = verify_token(auth_token)
        if username:
            return username, ''
    return st.session_state.get('saved_username', ''), ''


# 登录验证函数
def authenticate(username, password, remember_password=False):
    if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == password:
        set_login_status(username, remember_password)
        return True
    return False


def get_score_emoji(score):
    if str(SCORE_EMOJI_PARAM).strip() in {"0", "false", "False", "no", "off", "OFF"}:
        return ""

    if pd.isna(score):
        return ""
    if score >= 90:
        return "🟢"
    if score >= 75:
        return "😊"
    if score >= 60:
        return "😐"
    return "⚠️"


def get_score_color(score):
    if pd.isna(score):
        return ""
    if score > REVIEW_SCORE_HIGH_THRESHOLD:
        return HIGH_SCORE_COLOR
    if score < REVIEW_SCORE_LOW_THRESHOLD:
        return LOW_SCORE_COLOR
    return ""


def build_score_link(score, url):
    score_text = "" if pd.isna(score) else f"{float(score):.0f}"
    emoji = get_score_emoji(score)
    display_text = f"{emoji} {score_text}".strip() or "查看详情"
    safe_text = html.escape(display_text)
    safe_url = html.escape(str(url) if pd.notna(url) else "#", quote=True)
    return (
        f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
        f'style="text-decoration:none;color:inherit;font-weight:600;">{safe_text}</a>'
    )


def build_tooltip_cell(value):
    if pd.isna(value):
        return ""
    value_str = str(value)
    escaped = html.escape(value_str)
    return f'<div title="{escaped}" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{escaped}</div>'


def build_indexed_html_table(df, ordered_columns, headers, score_column=None):
    header_html = "".join(
        f'<th>{html.escape(title)}<div class="resize-handle"></div></th>'
        for title in headers
    )
    rows_html = []

    for index, row in df.reset_index(drop=True).iterrows():
        row_cells = [f'<td title="{index + 1}">{index + 1}</td>']
        for col in ordered_columns:
            value = row.get(col, "")
            cell_style = ""
            cell_content = build_tooltip_cell(value)
            if col == score_column:
                cell_color = get_score_color(value)
                if cell_color:
                    cell_style = f' style="background-color: {cell_color};"'
                cell_content = build_score_link(value, row.get("url", "#"))
            row_cells.append(f"<td{cell_style}>{cell_content}</td>")
        rows_html.append(f"<tr>{''.join(row_cells)}</tr>")

    return f"""
    <div class="review-table-wrapper">
        <table class="review-table">
            <thead>
                <tr><th>序号<div class="resize-handle"></div></th>{header_html}</tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
        </table>
    </div>
    """


# 获取数据函数
def get_data(service_func, authors=None, project_names=None, updated_at_gte=None, updated_at_lte=None, columns=None):
    df = service_func(authors=authors, project_names=project_names, updated_at_gte=updated_at_gte,
                      updated_at_lte=updated_at_lte)

    if df.empty:
        return pd.DataFrame(columns=columns)

    if "updated_at" in df.columns:
        df["updated_at"] = df["updated_at"].apply(
            lambda ts: datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(ts, (int, float)) else ts
        )

    def format_delta(row):
        if not math.isnan(row['additions']) and not math.isnan(row['deletions']):
            return f"+{int(row['additions'])}  -{int(row['deletions'])}"
        else:
            return ""

    if "additions" in df.columns and "deletions" in df.columns:
        df["delta"] = df.apply(format_delta, axis=1)
    else:
        df["delta"] = ""

    data = df[columns]
    return data


# 隐藏默认的Streamlit菜单和页眉
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        div.block-container {padding-top: 0rem;}
    </style>
    """, unsafe_allow_html=True)

# 自定义CSS样式
st.markdown(
    """
    <style>
    .main {
        background-color: #f0f2f6;
        padding-top: 0rem;
    }
    .stButton>button {
        background-color: #4CAF50;
        color: white;
        border-radius: 20px;
        padding: 0.5rem 2rem;
        border: none;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #45a049;
        box-shadow: 0 2px 5px rgba(0,0,0,0.2);
        color: #ffffff;  /* 设置悬停时的文字颜色为白色 */
    }

    .stTextInput>div>div>input {
        border: 1px solid #ccc;
        border-radius: 4px;
        padding: 0.5rem;
    }
    .stCheckbox>div>div>input {
        accent-color: #4CAF50;
    }
    .stDataFrame {
        border: 1px solid #ddd;
        border-radius: 4px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .stMarkdown {font-size: 18px;}
    .login-title {
        text-align: center;
        color: #2E4053;
        margin: 0.5rem 0;
        font-size: 2.2rem;
        font-weight: bold;
    }
    .login-container {
        background-color: white;
        border-radius: 15px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        margin-top: 0rem;
    }
    .platform-icon {
        font-size: 3.5rem;
        margin-bottom: 0.5rem;
        text-align: center;
    }
    /* Pro 版链接 - 与退出登录按钮同高同风格 */
    a.pro-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0.5rem 2rem;
        background: linear-gradient(135deg, #5b6bc0 0%, #7c4dff 100%);
        color: #fff !important;
        text-decoration: none;
        border-radius: 20px;
        font-size: 1rem;
        font-weight: 500;
        transition: all 0.3s ease;
        border: none;
        box-sizing: border-box;
        min-height: 2.25rem;
        line-height: 1.5;
        white-space: nowrap;
    }
    a.pro-link:hover {
        box-shadow: 0 2px 5px rgba(0,0,0,0.2);
        color: #fff !important;
    }
    .pro-link-wrap {
        display: flex;
        align-items: center;
        justify-content: flex-start;
        margin-left: 0.5rem;
        min-width: 0;
        overflow: hidden;
    }
    .pro-link-wrap .pro-link {
        max-width: 100%;
    }
    .review-table-wrapper {
        overflow-x: auto;
        overflow-y: auto;
        margin-top: 1rem;
        border: 1px solid #ddd;
        border-radius: 8px;
        background: #fff;
        max-height: 480px; /* ~10 rows: 10 × 44px per row + header */
    }
    .review-table-wrapper::-webkit-scrollbar {
        width: 16px;
        height: 16px;
    }
    .review-table-wrapper::-webkit-scrollbar-track {
        background: #f1f3f5;
        border-radius: 8px;
    }
    .review-table-wrapper::-webkit-scrollbar-thumb {
        background: #b8c0cc;
        border-radius: 8px;
    }
    .review-table-wrapper::-webkit-scrollbar-thumb:hover {
        background: #98a2b3;
    }
    .review-table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
    }
    .review-table thead th {
        position: sticky;
        top: 0;
        background: #f8f9fb;
        z-index: 1;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        user-select: none;
    }
    .review-table th,
    .review-table td {
        border: 1px solid #e5e7eb;
        padding: 10px 12px;
        text-align: left;
        max-width: 220px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .resize-handle {
        position: absolute;
        top: 0;
        right: 0;
        width: 5px;
        height: 100%;
        cursor: col-resize;
        background: transparent;
        z-index: 2;
    }
    .resize-handle:hover {
        background: rgba(0, 0, 0, 0.15);
    }
    </style>
    <script>
    (function() {
        var MIN_COL_WIDTH = 40;
        var _rs = {active: false, startX: 0, startW: 0, th: null};
        function initTable(table) {
            if (table.dataset.resizeReady) return;
            table.dataset.resizeReady = '1';
            table.querySelectorAll('thead th').forEach(function(th) {
                var handle = th.querySelector('.resize-handle');
                if (!handle) return;
                handle.addEventListener('mousedown', function(e) {
                    _rs.active = true;
                    _rs.startX = e.clientX;
                    _rs.startW = th.getBoundingClientRect().width;
                    _rs.th = th;
                    document.body.style.cursor = 'col-resize';
                    document.body.style.userSelect = 'none';
                    e.preventDefault();
                    e.stopPropagation();
                });
            });
        }
        document.addEventListener('mousemove', function(e) {
            if (!_rs.active || !_rs.th) return;
            var newW = Math.max(MIN_COL_WIDTH, _rs.startW + (e.clientX - _rs.startX));
            _rs.th.style.width = newW + 'px';
            _rs.th.style.minWidth = newW + 'px';
            _rs.th.style.maxWidth = newW + 'px';
        });
        document.addEventListener('mouseup', function() {
            if (_rs.active) {
                _rs.active = false;
                _rs.th = null;
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
            }
        });
        var _obs = new MutationObserver(function() {
            document.querySelectorAll('.review-table').forEach(initTable);
        });
        _obs.observe(document.body, {childList: true, subtree: true});
        document.querySelectorAll('.review-table').forEach(initTable);
    })();
    </script>
    """,
    unsafe_allow_html=True
)


# 登录界面
def login_page():
    # 使用 st.columns 创建居中布局
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown('<div class="login-container">', unsafe_allow_html=True)
        st.markdown('<div class="platform-icon">🤖</div>', unsafe_allow_html=True)
        st.markdown('<h1 class="login-title">AI代码审查平台</h1>', unsafe_allow_html=True)

        # 如果用户名和密码都为 'admin'，提示用户修改密码
        if DASHBOARD_USER == "admin" and DASHBOARD_PASSWORD == "admin":
            st.warning(
                "安全提示：检测到默认用户名和密码为 'admin'，存在安全风险！\n\n"
                "请立即修改：\n"
                "1. 打开 `.env` 文件\n"
                "2. 修改 `DASHBOARD_USER` 和 `DASHBOARD_PASSWORD` 变量\n"
                "3. 保存并重启应用"
            )
            st.write(f"当前用户名: `{DASHBOARD_USER}`, 当前密码: `{DASHBOARD_PASSWORD}`")

        # 获取保存的用户名和密码
        saved_username, saved_password = get_saved_credentials()

        # 创建一个form，支持回车提交
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("👤 用户名", value=saved_username)
            password = st.text_input("🔑 密码", type="password", value=saved_password)
            remember_password = st.checkbox("记住密码", value=bool(saved_username))
            submit = st.form_submit_button("登 录")

            if submit:
                if authenticate(username, password, remember_password):
                    st.rerun()  # 重新运行应用以显示主要内容
                else:
                    st.error("用户名或密码错误")
        st.markdown('</div>', unsafe_allow_html=True)


# 生成项目提交数量图表
def generate_project_count_chart(df):
    if df.empty:
        st.info("没有数据可供展示")
        return

    # 计算每个项目的提交数量
    project_counts = df['project_name'].value_counts().reset_index()
    project_counts.columns = ['project_name', 'count']

    # 生成颜色列表，每个项目一个颜色
    colors = plt.colormaps['tab20'].resampled(len(project_counts))

    # 显示提交数量柱状图
    fig1, ax1 = plt.subplots(figsize=(10, 6))
    ax1.bar(
        project_counts['project_name'],
        project_counts['count'],
        color=[colors(i) for i in range(len(project_counts))]
    )
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.xticks(rotation=45, ha='right', fontsize=26)
    plt.tight_layout()
    st.pyplot(fig1)


# 生成项目平均分数图表
def generate_project_score_chart(df):
    if df.empty:
        st.info("没有数据可供展示")
        return

    # 计算每个项目的平均分数
    project_scores = df.groupby('project_name')['score'].mean().reset_index()
    project_scores.columns = ['project_name', 'average_score']

    # 生成颜色列表，每个项目一个颜色
    # colors = plt.cm.get_cmap('Accent', len(project_scores))  # 使用'tab20'颜色映射，适合分类数据
    colors = plt.colormaps['Accent'].resampled(len(project_scores))
    # 显示平均分数柱状图
    fig2, ax2 = plt.subplots(figsize=(10, 6))
    ax2.bar(
        project_scores['project_name'],
        project_scores['average_score'],
        color=[colors(i) for i in range(len(project_scores))]
    )
    ax2.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.xticks(rotation=45, ha='right', fontsize=26)
    plt.tight_layout()
    st.pyplot(fig2)


# 生成人员提交数量图表
def generate_author_count_chart(df):
    if df.empty:
        st.info("没有数据可供展示")
        return

    # 计算每个人员的提交数量
    author_counts = df['author_name'].value_counts().reset_index()
    author_counts.columns = ['author_name', 'count']

    # 生成颜色列表，每个项目一个颜色
    colors = plt.colormaps['Paired'].resampled(len(author_counts))
    # 显示提交数量柱状图
    fig1, ax1 = plt.subplots(figsize=(10, 6))
    ax1.bar(
        author_counts['author_name'],
        author_counts['count'],
        color=[colors(i) for i in range(len(author_counts))]
    )
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.xticks(rotation=45, ha='right', fontsize=26)
    plt.tight_layout()
    st.pyplot(fig1)
    plt.close(fig1)


# 生成人员平均分数图表
def generate_author_score_chart(df):
    if df.empty:
        st.info("没有数据可供展示")
        return

    # 计算每个人员的平均分数
    author_scores = df.groupby('author_name')['score'].mean().reset_index()
    author_scores.columns = ['author_name', 'average_score']

    # 显示平均分数柱状图
    fig2, ax2 = plt.subplots(figsize=(10, 6))
    # 生成颜色列表，每个项目一个颜色
    colors = plt.colormaps['Pastel1'].resampled(len(author_scores))
    ax2.bar(
        author_scores['author_name'],
        author_scores['average_score'],
        color=[colors(i) for i in range(len(author_scores))]
    )
    ax2.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.xticks(rotation=45, ha='right', fontsize=26)
    plt.tight_layout()
    st.pyplot(fig2)



def generate_author_code_line_chart(df):
    if df.empty:
        st.info("没有数据可供展示")
        return
        # 检查必要的列是否存在

    if 'additions' not in df.columns or 'deletions' not in df.columns:
        st.warning("无法生成代码行数图表：缺少必要的数据列")
        return
        # 计算每个人员的代码行数
    author_code_lines_add = df.groupby('author_name')['additions'].sum().reset_index()
    author_code_lines_add.columns = ['author_name', 'additions']
    author_code_lines_del = df.groupby('author_name')['deletions'].sum().reset_index()
    author_code_lines_del.columns = ['author_name', 'deletions']
    # 显示代码行数柱状图
    fig3, ax3 = plt.subplots(figsize=(10, 6))
    ax3.bar(
        author_code_lines_add['author_name'],
        author_code_lines_add['additions'],
        color=(0.7, 1, 0.7)
    )
    ax3.bar(
        author_code_lines_del['author_name'],
        -author_code_lines_del['deletions'],
        color=(1, 0.7, 0.7)
    )
    plt.xticks(rotation=45, ha='right', fontsize=26)
    plt.tight_layout()
    st.pyplot(fig3)


# 退出登录函数
def logout():
    # 清除session状态
    st.session_state['login_status'] = False
    st.session_state.pop('username', None)
    st.session_state.pop('saved_username', None)

    # 清除cookie
    if 'auth_token' in cookies:
        del cookies['auth_token']
    cookies.save()

    st.rerun()


# Pro 版文档链接（登录后展示）
PRO_VERSION_URL = "https://github.com/sunmh207/AI-Codereview-Gitlab/blob/main/doc/pro.md"


# 主要内容
def main_page():
    # 顶部导航：标题、留白、退出登录与 Pro 版（两按钮不重叠，留出间距）
    col_title, col_space, col_actions = st.columns([5, 1.5, 3.5])
    with col_title:
        st.markdown("#### 📊 代码审查统计")
    with col_actions:
        # 两列分别放退出登录、Pro 版，比例略偏右列以容纳较长文案
        sub_col_logout, sub_col_pro = st.columns([1, 1.15])
        with sub_col_logout:
            if st.button("退出登录", key="logout_button", use_container_width=True):
                logout()
        with sub_col_pro:
            st.markdown(
                '<div class="pro-link-wrap">'
                '<a href="' + PRO_VERSION_URL + '" target="_blank" rel="noopener noreferrer" class="pro-link">开源版 VS Pro 版</a>'
                '</div>',
                unsafe_allow_html=True
            )

    current_date = datetime.date.today()
    start_date_default = current_date - datetime.timedelta(days=7)

    # 根据环境变量决定是否显示 push_tab
    show_push_tab = os.environ.get('PUSH_REVIEW_ENABLED', '0') == '1'

    if show_push_tab:
        mr_tab, push_tab = st.tabs(["合并请求", "代码推送"])
    else:
        mr_tab = st.container()

    def display_data(tab, service_func, columns, ordered_columns, headers, score_column):
        with tab:
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                start_date = st.date_input("开始日期", start_date_default, key=f"{tab}_start_date")
            with col2:
                end_date = st.date_input("结束日期", current_date, key=f"{tab}_end_date")

            start_datetime = datetime.datetime.combine(start_date, datetime.time.min)
            end_datetime = datetime.datetime.combine(end_date, datetime.time.max)

            data = get_data(service_func, updated_at_gte=int(start_datetime.timestamp()),
                            updated_at_lte=int(end_datetime.timestamp()), columns=columns)
            df = pd.DataFrame(data)

            unique_authors = sorted(df["author_name"].dropna().unique().tolist()) if not df.empty else []
            unique_projects = sorted(df["project_name"].dropna().unique().tolist()) if not df.empty else []
            with col3:
                authors = st.multiselect("开发者", unique_authors, default=[], key=f"{tab}_authors")
            with col4:
                project_names = st.multiselect("项目名称", unique_projects, default=[], key=f"{tab}_projects")

            data = get_data(service_func, authors=authors, project_names=project_names,
                            updated_at_gte=int(start_datetime.timestamp()),
                            updated_at_lte=int(end_datetime.timestamp()), columns=columns)
            df = pd.DataFrame(data)

            if df.empty:
                st.info("没有数据可供展示")
            else:
                st.markdown(
                    build_indexed_html_table(df, ordered_columns, headers, score_column),
                    unsafe_allow_html=True
                )

            total_records = len(df)
            average_score = df["score"].mean() if not df.empty else 0
            st.markdown(f"**总记录数:** {total_records}，**平均得分:** {average_score:.2f}")

            # 创建2x2网格布局展示四个图表
            row1, row2, row3, row4 = st.columns(4)
            with row1:
                st.markdown("<div style='text-align: center; font-size: 20px;'><b>项目提交统计</b></div>",
                            unsafe_allow_html=True)
                generate_project_count_chart(df)
            with row2:
                st.markdown("<div style='text-align: center; font-size: 20px;'><b>项目平均得分</b></div>",
                            unsafe_allow_html=True)
                generate_project_score_chart(df)
            with row3:
                st.markdown("<div style='text-align: center; font-size: 20px;'><b>开发者提交统计</b></div>",
                            unsafe_allow_html=True)
                generate_author_count_chart(df)
            with row4:
                st.markdown("<div style='text-align: center; font-size: 20px;'><b>开发者平均得分</b></div>",
                            unsafe_allow_html=True)
                generate_author_score_chart(df)

            row5, row6, row7, row8 = st.columns(4)
            with row5:
                st.markdown("<div style='text-align: center;'><b>人员代码变更行数</b></div>", unsafe_allow_html=True)
                # 只有当 additions 和 deletions 列都存在时才显示代码行数图表
                if 'additions' in df.columns and 'deletions' in df.columns:
                    generate_author_code_line_chart(df)
                else:
                    st.info("无法显示代码行数图表：缺少必要的数据列")

    # Merge Request 数据展示
    mr_columns = [
        "commit_messages", "score", "author_name", "project_name", "source_branch", "target_branch",
        "updated_at", "delta", "url", 'additions', 'deletions', "author"
    ]
    mr_ordered_columns = [
        "commit_messages", "score", "author_name", "project_name", "source_branch", "target_branch",
        "updated_at", "delta"
    ]
    mr_headers = ["提交信息", "得分", "开发者", "项目名称", "源分支", "目标分支", "更新时间", "delta"]

    display_data(mr_tab, ReviewService().get_mr_review_logs, mr_columns, mr_ordered_columns, mr_headers, "score")

    # Push 数据展示
    if show_push_tab:
        push_columns = [
            "commit_messages", "score", "author_name", "project_name", "branch", "updated_at", "delta",
            'additions', 'deletions', "author"
        ]
        push_ordered_columns = [
            "commit_messages", "score", "author_name", "project_name", "branch", "updated_at", "delta"
        ]
        push_headers = ["提交信息", "得分", "开发者", "项目名称", "分支", "更新时间", "delta"]

        display_data(push_tab, ReviewService().get_push_review_logs, push_columns, push_ordered_columns, push_headers,
                     "score")


# 应用入口
if check_login_status():
    main_page()
else:
    login_page()
