import sqlite3

import pandas as pd

from biz.entity.review_entity import MergeRequestReviewEntity, PushReviewEntity


class ReviewService:
    DB_FILE = "data/data.db"

    @staticmethod
    def init_db():
        """初始化数据库及表结构"""
        try:
            with sqlite3.connect(ReviewService.DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                        CREATE TABLE IF NOT EXISTS mr_review_log (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            project_name TEXT,
                            author TEXT,
                            author_name TEXT,
                            source_branch TEXT,
                            target_branch TEXT,
                            updated_at INTEGER,
                            commit_messages TEXT,
                            score INTEGER,
                            url TEXT,
                            review_result TEXT,
                            additions INTEGER DEFAULT 0,
                            deletions INTEGER DEFAULT 0,
                            last_commit_id TEXT DEFAULT '',
                            mr_iid INTEGER DEFAULT NULL
                        )
                    ''')
                cursor.execute('''
                        CREATE TABLE IF NOT EXISTS push_review_log (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            project_name TEXT,
                            author TEXT,
                            author_name TEXT,
                            branch TEXT,
                            updated_at INTEGER,
                            commit_messages TEXT,
                            score INTEGER,
                            review_result TEXT,
                            additions INTEGER DEFAULT 0,
                            deletions INTEGER DEFAULT 0
                        )
                    ''')
                # 确保旧版本的mr_review_log、push_review_log表添加additions、deletions列
                tables = ["mr_review_log", "push_review_log"]
                columns = ["additions", "deletions"]
                for table in tables:
                    cursor.execute(f"PRAGMA table_info({table})")
                    current_columns = [col[1] for col in cursor.fetchall()]
                    for column in columns:
                        if column not in current_columns:
                            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER DEFAULT 0")

                # 为旧版本的mr_review_log表添加last_commit_id字段
                mr_columns = [
                    {
                        "name": "last_commit_id",
                        "type": "TEXT",
                        "default": "''"
                    },
                    {
                        "name": "mr_iid",
                        "type": "INTEGER",
                        "default": "NULL"
                    }
                ]
                cursor.execute(f"PRAGMA table_info('mr_review_log')")
                current_columns = [col[1] for col in cursor.fetchall()]
                for column in mr_columns:
                    if column.get("name") not in current_columns:
                        cursor.execute(f"ALTER TABLE mr_review_log ADD COLUMN {column.get('name')} {column.get('type')} "
                                       f"DEFAULT {column.get('default')}")

                # 为旧版本的mr_review_log、push_review_log表添加author_name字段
                for table in ["mr_review_log", "push_review_log"]:
                    cursor.execute(f"PRAGMA table_info({table})")
                    current_columns = [col[1] for col in cursor.fetchall()]
                    if "author_name" not in current_columns:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN author_name TEXT DEFAULT NULL")

                conn.commit()
                # 添加时间字段索引（默认查询就需要时间范围）
                conn.execute('CREATE INDEX IF NOT EXISTS idx_push_review_log_updated_at ON '
                             'push_review_log (updated_at);')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_mr_review_log_updated_at ON mr_review_log (updated_at);')
        except sqlite3.DatabaseError as e:
            print(f"Database initialization failed: {e}")

    @staticmethod
    def insert_mr_review_log(entity: MergeRequestReviewEntity):
        """插入合并请求审核日志"""
        try:
            with sqlite3.connect(ReviewService.DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                                INSERT INTO mr_review_log (project_name, author, author_name, source_branch, target_branch, 
                                updated_at, commit_messages, score, url, review_result, additions, deletions, 
                                last_commit_id, mr_iid)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''',
                               (entity.project_name, entity.author, entity.author_name or None,
                                entity.source_branch,
                                entity.target_branch, entity.updated_at, entity.commit_messages, entity.score,
                                entity.url, entity.review_result, entity.additions, entity.deletions,
                                entity.last_commit_id, entity.mr_iid))
                conn.commit()
        except sqlite3.DatabaseError as e:
            print(f"Error inserting review log: {e}")

    @staticmethod
    def get_mr_review_logs(authors: list = None, project_names: list = None, updated_at_gte: int = None,
                           updated_at_lte: int = None) -> pd.DataFrame:
        """获取符合条件的合并请求审核日志"""
        try:
            with sqlite3.connect(ReviewService.DB_FILE) as conn:
                query = """
                            SELECT project_name, author, COALESCE(NULLIF(author_name, ''), author) AS author_name,
                            source_branch, target_branch, updated_at, commit_messages, score, url, review_result, additions, deletions
                            FROM mr_review_log
                            WHERE 1=1
                            """
                params = []

                if authors:
                    placeholders = ','.join(['?'] * len(authors))
                    query += f" AND COALESCE(NULLIF(author_name, ''), author) IN ({placeholders})"
                    params.extend(authors)

                if project_names:
                    placeholders = ','.join(['?'] * len(project_names))
                    query += f" AND project_name IN ({placeholders})"
                    params.extend(project_names)

                if updated_at_gte is not None:
                    query += " AND updated_at >= ?"
                    params.append(updated_at_gte)

                if updated_at_lte is not None:
                    query += " AND updated_at <= ?"
                    params.append(updated_at_lte)
                query += " ORDER BY updated_at DESC"
                df = pd.read_sql_query(sql=query, con=conn, params=params)
            return df
        except sqlite3.DatabaseError as e:
            print(f"Error retrieving review logs: {e}")
            return pd.DataFrame()

    @staticmethod
    def check_mr_last_commit_id_exists(project_name: str, source_branch: str, target_branch: str,
                                       last_commit_id: str, mr_iid: int = None) -> bool:
        """检查指定项目的Merge Request是否已经存在相同的last_commit_id。

        当 mr_iid 可用时，以 (project_name, mr_iid) 作为 MR 唯一标识进行查询，
        避免同名分支重复开/关 MR 时误命中历史记录；否则回退到基于分支名的查询。
        """
        try:
            with sqlite3.connect(ReviewService.DB_FILE) as conn:
                cursor = conn.cursor()
                if mr_iid is not None:
                    cursor.execute('''
                        SELECT COUNT(*) FROM mr_review_log
                        WHERE project_name = ? AND mr_iid = ? AND last_commit_id = ?
                    ''', (project_name, mr_iid, last_commit_id))
                else:
                    cursor.execute('''
                        SELECT COUNT(*) FROM mr_review_log 
                        WHERE project_name = ? AND source_branch = ? AND target_branch = ? AND last_commit_id = ?
                    ''', (project_name, source_branch, target_branch, last_commit_id))
                count = cursor.fetchone()[0]
                return count > 0
        except sqlite3.DatabaseError as e:
            print(f"Error checking last_commit_id: {e}")
            return False

    @staticmethod
    def get_last_mr_review_commit_id(project_name: str, source_branch: str, target_branch: str,
                                     mr_iid: int = None) -> str:
        """获取最近一次 MR 审核记录的 last_commit_id，若无则返回空字符串。

        当 mr_iid 可用时，以 (project_name, mr_iid) 作为 MR 唯一标识进行查询，
        避免同名分支重复开/关 MR 时误命中历史记录；否则回退到基于分支名的查询。
        """
        try:
            with sqlite3.connect(ReviewService.DB_FILE) as conn:
                cursor = conn.cursor()
                if mr_iid is not None:
                    cursor.execute('''
                        SELECT last_commit_id FROM mr_review_log
                        WHERE project_name = ? AND mr_iid = ?
                        ORDER BY updated_at DESC
                        LIMIT 1
                    ''', (project_name, mr_iid))
                else:
                    cursor.execute('''
                        SELECT last_commit_id FROM mr_review_log
                        WHERE project_name = ? AND source_branch = ? AND target_branch = ?
                        ORDER BY updated_at DESC
                        LIMIT 1
                    ''', (project_name, source_branch, target_branch))
                row = cursor.fetchone()
                return row[0] if row and row[0] else ''
        except sqlite3.DatabaseError as e:
            print(f"Error getting last mr review commit id: {e}")
            return ''

    @staticmethod
    def insert_push_review_log(entity: PushReviewEntity):
        """插入推送审核日志"""
        try:
            with sqlite3.connect(ReviewService.DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                                INSERT INTO push_review_log (project_name, author, author_name, branch, updated_at, commit_messages, score, review_result, additions, deletions)
                                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''',
                               (entity.project_name, entity.author, entity.author_name or None,
                                entity.branch,
                                entity.updated_at, entity.commit_messages, entity.score,
                                entity.review_result, entity.additions, entity.deletions))
                conn.commit()
        except sqlite3.DatabaseError as e:
            print(f"Error inserting review log: {e}")

    @staticmethod
    def get_push_review_logs(authors: list = None, project_names: list = None, updated_at_gte: int = None,
                             updated_at_lte: int = None) -> pd.DataFrame:
        """获取符合条件的推送审核日志"""
        try:
            with sqlite3.connect(ReviewService.DB_FILE) as conn:
                # 基础查询
                query = """
                    SELECT project_name, author, COALESCE(NULLIF(author_name, ''), author) AS author_name,
                    branch, updated_at, commit_messages, score, review_result, additions, deletions
                    FROM push_review_log
                    WHERE 1=1
                """
                params = []

                # 动态添加 authors 条件
                if authors:
                    placeholders = ','.join(['?'] * len(authors))
                    query += f" AND COALESCE(NULLIF(author_name, ''), author) IN ({placeholders})"
                    params.extend(authors)

                if project_names:
                    placeholders = ','.join(['?'] * len(project_names))
                    query += f" AND project_name IN ({placeholders})"
                    params.extend(project_names)

                # 动态添加 updated_at_gte 条件
                if updated_at_gte is not None:
                    query += " AND updated_at >= ?"
                    params.append(updated_at_gte)

                # 动态添加 updated_at_lte 条件
                if updated_at_lte is not None:
                    query += " AND updated_at <= ?"
                    params.append(updated_at_lte)

                # 按 updated_at 降序排序
                query += " ORDER BY updated_at DESC"

                # 执行查询
                df = pd.read_sql_query(sql=query, con=conn, params=params)
                return df
        except sqlite3.DatabaseError as e:
            print(f"Error retrieving push review logs: {e}")
            return pd.DataFrame()


# Initialize database
ReviewService.init_db()
