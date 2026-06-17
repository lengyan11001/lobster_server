# Comfly/Yunwu/OpenMind 代理接口占用 DB 连接问题交接

日期：2026-06-16
范围：`lobster_server` 正式环境后端 8000 卡死 / PostgreSQL 连接池耗尽问题

## 现象

正式环境后端出现过以下表现：

- `https://bhzn.top/api/edition`、`/openapi.json` 超时。
- H5 静态站仍可访问，说明不是整台机器宕机。
- 8000 端口大量 `CLOSE-WAIT`。
- PostgreSQL 中出现约 30 个 `idle in transaction`。
- 后端日志出现：

```text
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 60.00
```

当时后端 systemd 配置为：

```text
BACKEND_WORKERS=2
db_pool_size=5
db_max_overflow=10
```

因此主后端理论最大连接数是：

```text
2 workers * (5 + 10) = 30
```

这 30 个连接被占满后，`/auth/me`、轮询、心跳、系统配置等普通接口都无法拿到连接，最终表现为整站后端卡死。

## 已做的临时恢复

已经在正式环境定向部署过以下止血改动：

- `backend/app/db.py`
  - `SessionLocal(..., expire_on_commit=False, ...)`
  - `get_db()` 退出时如果事务仍打开，先 `rollback()` 再 `close()`
- `backend/app/api/h5_chat.py`
  - H5 媒体代理不再用 `Depends(get_db)` 持有 DB 到流式响应结束
- `backend/app/api/scheduled_tasks.py`
  - `ip_content_daily` 不再在 H5 请求里同步入队执行，交给后台 worker

恢复后观察：

- `/api/edition`、`/openapi.json` 恢复 200。
- 连接池不再持续 30 个 `idle in transaction`。
- 后续仍偶发 0-1 个短暂 `idle in transaction`，需要进一步修慢接口持库方式。

## 关键根因

用户提到的这些接口看起来是“纯代理上游”，但当前代码并不是完全不访问数据库：

```text
POST /api/comfly-proxy/v1/video/create
POST /api/comfly-proxy/v1/images/generations
POST /api/comfly-proxy/v2/videos/generations
```

对应代码位置：

- `backend/app/api/comfly_proxy.py`
  - `proxy_images_generations()`，约 line 1299
  - `proxy_videos_generations_submit()`，约 line 1731
  - `proxy_yunwu_video_create()`，约 line 1995

这些函数签名中都有：

```python
current_user: User = Depends(get_current_user)
db: Session = Depends(get_db)
```

而 `get_current_user()` 也会通过 `Depends(get_db)` 查询用户：

- `backend/app/api/auth.py`
  - `get_current_user()`，约 line 354
  - `user = db.query(User).filter(User.id == user_id).first()`

所以即使业务主体是转发上游，请求一进来仍会打开 DB session，并且 FastAPI 依赖的 session 生命周期会跟随整个请求。

这几个接口内部还会做：

- 鉴权查询用户
- 手机/小程序用户映射到 online 用户
- 预扣费
- 上游失败退款
- 成功或失败写模型调用记录
- 图片生成成功后保存素材记录

相关函数：

- `backend/app/api/comfly_proxy.py`
  - `_do_pre_deduct()`，约 line 184
  - `_do_settle()`，约 line 214
  - `_do_full_refund()`，约 line 267
  - `_save_generated_images_best_effort()`，约 line 587
- `backend/app/services/model_usage_monitor.py`
  - `log_model_usage_event()`

问题不在于“是否有必要查数据库”，而在于：

> 当前实现把 DB session 贯穿了整个慢上游调用周期。上游图片/视频接口可能等待 60-300 秒，期间 DB 连接可能一直被这个请求占住。

当多个用户同时发起爆款 TVC、图片生成、视频提交等慢任务时，连接池很容易被这些等待中的请求占满。

## 为什么不能只加连接池

当前 PostgreSQL：

```text
max_connections = 100
```

主后端当前最大 30 个连接。如果简单加大，例如每 worker 20+20，两个 worker 就可能到 80，再加上：

- H5 服务
- background worker
- MCP/其他服务
- 管理后台/监控/psql

反而可能把 PostgreSQL 总连接打满。

所以连接池可以后续评估，但不是根治方案。根治应先减少慢请求持有 DB 连接的时间。

## 建议改法

### 方案 A：最小改动，先修慢代理接口

目标：

> 上游 HTTP 等待期间不持有 DB session。

建议仅先改这些慢接口：

- `proxy_images_generations()`
- `proxy_videos_generations_submit()`
- `proxy_yunwu_video_create()`
- 可顺手检查 `proxy_openmind_video_submit()`，它也有相同模式

处理方式：

1. 不再使用：

```python
current_user: User = Depends(get_current_user)
db: Session = Depends(get_db)
```

2. 新增一个短事务 helper，例如：

```python
def _resolve_proxy_billing_user_ids(token: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        current_user = _get_user_from_token_with_db(db, token)
        billing_user = online_user_for_mobile_user(db, current_user)
        return int(current_user.id), int(billing_user.id)
    finally:
        db.close()
```

3. 鉴权完成后只保留 `current_user_id`、`billing_user_id`，不要把 ORM `User` 对象带到上游等待之后。

4. 扣费单独短 session：

```python
pre = _pre_deduct_by_user_id(
    billing_user_id,
    estimated,
    capability_id=...,
    model=...,
    endpoint=...,
)
```

内部重新查 `User`，扣费后立即 `commit/close`。

5. 调上游时不持有 DB：

```python
resp = await _comfly_request(...)
```

6. 上游返回后，写用量记录、保存素材、失败退款都各自用短 session。

### 方案 B：完善计费 API，减少 ORM 对象传递

现在 `_do_pre_deduct()`、`_do_full_refund()`、`_do_settle()` 接收的是 `Session + User ORM 对象`。

建议新增按 `user_id` 操作的版本：

```python
def _do_pre_deduct_by_user_id(user_id: int, credits: int, *, ...) -> Decimal
def _do_full_refund_by_user_id(user_id: int, pre: Decimal, *, ...) -> None
def _do_settle_by_user_id(user_id: int, pre: Decimal, actual: int, *, ...) -> None
```

这些函数内部自己创建短 session：

```python
db = SessionLocal()
try:
    user = db.query(User).filter(User.id == user_id).first()
    ...
    db.commit()
finally:
    db.close()
```

这样慢代理接口不会在函数之间传递长期存活的 session。

### 方案 C：加数据库兜底保护

PostgreSQL 当前：

```text
idle_in_transaction_session_timeout = 0
statement_timeout = 0
lock_timeout = 0
```

建议设置一个兜底值，避免未来某个漏事务无限占连接：

```sql
ALTER DATABASE lobster SET idle_in_transaction_session_timeout = '180s';
```

如担心误杀，可以先设 `300s`，观察一段时间再收紧。

注意：这只是兜底，不替代代码修复。

## 需要重点检查的具体接口

### 1. 图片生成

文件：

```text
backend/app/api/comfly_proxy.py
```

函数：

```python
proxy_images_generations()
```

当前风险点：

- `current_user = Depends(get_current_user)` 打开 DB
- `db = Depends(get_db)` 打开 DB
- `_do_pre_deduct()` 在上游调用前执行
- `_comfly_request()` / `_openmind_image_request()` / `_yunwu_multipart_request()` 可能等待很久
- `_save_generated_images_best_effort()` 成功后写素材
- `log_model_usage_event(db, ...)` 复用同一个 session

改造后应确保：

- 上游调用前扣费完成并关闭 DB
- 上游等待期间没有 DB session
- 保存素材时新开短 session
- 写 usage 时新开短 session，或者直接调用 `log_model_usage_event(None, ...)`

### 2. Comfly/Veo 视频提交

函数：

```python
proxy_videos_generations_submit()
```

当前风险点：

- `_do_pre_deduct(db, current_user, ...)`
- 等待 `_comfly_request()` 或 `_submit_comfly_grok15_video()`
- 成功后 `log_model_usage_event(db, ...)`
- 失败后 `_do_full_refund(db, current_user, ...)`

改造后应确保：

- pre deduct 使用短 session
- 上游请求期间不持有 DB
- 成功/失败记录分别短 session
- refund 使用短 session

### 3. Yunwu 视频提交

函数：

```python
proxy_yunwu_video_create()
```

当前风险点同上：

- 先扣费
- 上游 `_yunwu_request()` 等待
- 成功/失败写 usage
- 失败退款

同样需要短 session 化。

### 4. OpenMind 视频提交

函数：

```python
proxy_openmind_video_submit()
```

虽然这次用户点名的不是它，但它也有相同结构：

```python
current_user: User = Depends(get_current_user)
db: Session = Depends(get_db)
```

并且也会：

- online 用户映射
- 预扣
- 上游 submit
- 失败退款
- usage 记录

建议一起修。

## 验证方法

### 单次功能验证

部署后至少验证：

```text
POST /api/comfly-proxy/v1/images/generations
POST /api/comfly-proxy/v2/videos/generations
POST /api/comfly-proxy/v1/video/create
```

要求：

- 鉴权仍有效
- 积分不足仍返回 402
- 成功时仍预扣/记录 usage
- 失败时仍退款
- 图片生成后素材仍能入库

### 并发验证

用 20-40 个并发请求打慢代理接口，观察：

```bash
sudo -u postgres psql -d lobster -c "
select state, count(*)
from pg_stat_activity
where datname='lobster'
group by state
order by 2 desc;
"
```

预期：

- 上游等待期间不应出现大量 `idle in transaction`
- 普通接口 `/api/edition`、`/auth/me` 不应被拖慢到超时

### 线上观察命令

```bash
curl -sS -m 8 -o /tmp/lobster_probe.out \
  -w 'backend_api code=%{http_code} time=%{time_total}\n' \
  http://127.0.0.1:8000/api/edition

ss -antp | awk '$4 ~ /:8000$/ {c[$1]++} END {for (s in c) print s,c[s]}'

sudo -u postgres psql -d lobster -Atc "
select coalesce(state,'null'), count(*)
from pg_stat_activity
where datname='lobster'
group by state
order by 2 desc;
"

journalctl -u lobster-backend --since '10 minutes ago' --no-pager \
  | grep -E 'QueuePool|TimeoutError|Exception in ASGI application'
```

## 当前建议优先级

1. 先把 `comfly_proxy.py` 慢代理接口改成短事务。
2. 再给 PostgreSQL 加 `idle_in_transaction_session_timeout` 兜底。
3. 最后再根据真实并发考虑是否调整 `db_pool_size/db_max_overflow`。

不建议第一步就加大连接池。当前事故的核心是慢请求持有 DB 连接，不是数据库连接数天然不足。
