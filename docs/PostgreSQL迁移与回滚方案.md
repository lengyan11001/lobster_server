# PostgreSQL 迁移与回滚方案

推荐把生产库从 SQLite 切到 PostgreSQL。目标是提升并发写能力，同时保留快速回滚路径。

## 为什么选 PostgreSQL

- 支持多连接并发读写，避免 SQLite 单写锁。
- SQLAlchemy 原生支持，当前模型里的 `JSON`、`Numeric`、`DateTime` 都能直接映射。
- 后续做任务队列、统计报表、审计查询更稳。

## 切换原则

- SQLite 源库不做原地修改。
- 先停服务，复制 SQLite 备份，再导入 PostgreSQL。
- 导入后做表行数校验。
- 校验通过再改 `.env` 的 `DATABASE_URL` 并重启。
- 出问题时把 `.env` 切回 `sqlite:///./lobster.db`，重启即可回滚。

## 1. 安装 PostgreSQL

Ubuntu 示例：

```bash
sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib
```

创建库和用户：

```bash
sudo -u postgres psql
```

```sql
CREATE USER lobster WITH PASSWORD '请换成强密码';
CREATE DATABASE lobster OWNER lobster;
\q
```

连接串格式：

```text
postgresql+psycopg://lobster:请换成强密码@127.0.0.1:5432/lobster
```

## 2. 安装 Python 驱动

仓库 `requirements.txt` 已包含：

```text
psycopg[binary]>=3.2.0,<4
```

服务器执行：

```bash
cd /opt/lobster-server
.venv/bin/pip install -r requirements.txt
```

## 3. 迁移演练

先不要改 `.env`。

```bash
cd /opt/lobster-server
POSTGRES_URL='postgresql+psycopg://lobster:密码@127.0.0.1:5432/lobster'
.venv/bin/python scripts/migrate_sqlite_to_postgres.py \
  --sqlite ./lobster.db \
  --postgres "$POSTGRES_URL" \
  --create-tables \
  --truncate-target
```

脚本会：

1. 备份 SQLite 到 `db_backups/`。
2. 在 PostgreSQL 创建缺失表。
3. 清空 PostgreSQL 目标表。
4. 按 ORM 表依赖顺序复制数据。
5. 重置 PostgreSQL 自增序列。
6. 输出每张表的 SQLite/PostgreSQL 行数。

只有全部 `OK` 才能切库。

## 4. 正式切库

建议维护窗口执行，减少切换期间新写入。

```bash
cd /opt/lobster-server
sudo systemctl stop lobster-backend lobster-mcp lobster-h5 2>/dev/null || true
cp lobster.db "db_backups/lobster.db.before_pg_$(date +%Y%m%d_%H%M%S).bak"
```

重新导入一次，保证停机后的最终数据也进 PostgreSQL：

```bash
POSTGRES_URL='postgresql+psycopg://lobster:密码@127.0.0.1:5432/lobster'
.venv/bin/python scripts/migrate_sqlite_to_postgres.py \
  --sqlite ./lobster.db \
  --postgres "$POSTGRES_URL" \
  --create-tables \
  --truncate-target
```

修改 `.env`：

```env
DATABASE_URL=postgresql+psycopg://lobster:密码@127.0.0.1:5432/lobster
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=60
DB_POOL_RECYCLE=280
```

验证配置：

```bash
.venv/bin/python scripts/verify_database_url.py
```

启动：

```bash
sudo systemctl start lobster-backend lobster-mcp
sudo systemctl start lobster-h5 2>/dev/null || true
sudo systemctl status lobster-backend lobster-mcp --no-pager
```

## 5. 回滚

如果启动失败、接口异常或行数不一致：

```bash
cd /opt/lobster-server
sudo systemctl stop lobster-backend lobster-mcp lobster-h5 2>/dev/null || true
```

把 `.env` 改回：

```env
DATABASE_URL=sqlite:///./lobster.db
```

启动：

```bash
sudo systemctl start lobster-backend lobster-mcp
sudo systemctl start lobster-h5 2>/dev/null || true
```

因为 SQLite 源库没有被迁移脚本修改，回滚只需要切回连接串。

## 注意

- 切换后如果继续写入 PostgreSQL，再回滚 SQLite，会丢失切换期间的新写入；因此切库后先观察关键接口，再开放使用。
- 旧的 SQLite 排查脚本需要改成通过 SQLAlchemy 或 `psql` 查询。
- 后续如果开启多 worker，必须先确认 PostgreSQL 已稳定运行。
