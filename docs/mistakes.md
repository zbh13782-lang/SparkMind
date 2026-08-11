# 2026-08-11 Spark 集成实现踩坑记录

## 1. 测试类名必须 Test 开头

pytest 默认只发现 `Test*` 开头的类。写了 `class SparkJobModelTests` 和 `class SparkJobRunnerTests`，两个类的所有测试方法全部跳过，pytest 输出 `no tests ran`。

**修复**：改成 `class TestSparkJobModelTests(unittest.TestCase)` / `class TestSparkJobRunnerTests(unittest.IsolatedAsyncioTestCase)`。

**教训**：先跑一次 `pytest -q` 看 collected 数量，不要等写完全部测试才跑。

---

## 2. 异步测试必须继承 IsolatedAsyncioTestCase

`async def test_xxx` 写在了普通 class 里，pytest 不会自动运行。项目里所有异步测试都用 `unittest.IsolatedAsyncioTestCase`，复制粘贴时漏了。

**修复**：runner 测试继承 `unittest.IsolatedAsyncioTestCase`。

---

## 3. 漏 import unittest

改了类名加了 `(unittest.TestCase)` 和 `(unittest.IsolatedAsyncioTestCase)`，但没加 `import unittest`。NameError 直到跑测试才发现。

**修复**：补上 `import unittest`。

---

## 4.  unused  import 累积

registry.py 加了 `import asyncio`（写 `_run_spark_job` 时以为要用），后来改成 awaitable 返回模式不需要了，没删。ruff 报 `F401` 后才删掉。

test_spark_client.py 里有 `import inspect` 和 `import json`，实际没用到，ruff format --fix 时自动清理了。

**教训**：每改完一个文件立即跑 `ruff check`，不要攒到全部写完。

---

## 5. TOOL_DEFINITIONS 元素混用 tuple 和 dict

把 `run_spark_job` 的定义包了一层括号 `({...},)` 变成了 tuple，其他工具都是裸 dict。`context._tools_overview` 对每个元素调 `.get("function", {})`，tuple 没有 `.get()`，直接抛 `AttributeError: 'tuple' object has not attribute 'get'`。

这是导致运行时 `'tuple' object has no attribute 'get'` 错误的根因。

**修复**：去掉外层括号，改成和其他工具一致的裸 dict。

**教训**：往列表里加新元素时，照抄现有元素的类型，不要额外包一层。写完立刻跑全量测试（不只是新增的测试文件），因为工具注册是全局状态，影响所有路径。

---

## 6. Docker for Mac 不支持文件级 bind mount

`docker-compose.yml` 写了：
```yaml
- ./docker/spark-defaults.conf:/opt/spark/conf/spark-defaults.conf:ro
```

Docker for Mac 通过 gRPC-FUSE 做 bind mount，不支持将单个文件 mount 到文件（只支持目录 mount）。启动 worker 时报错：
```
error mounting .../docker/spark-defaults.conf to rootfs at /opt/spark/conf/spark-defaults.conf:
not a directory: Are you trying to mount a directory onto a file (or vice-versa)?
```

**修复**：去掉所有 `./docker/spark-defaults.conf` 的 volume mount，把配置改成环境变量注入（`SPARK_EVENTLOG_ENABLED` 等），效果等价，且 Docker for Mac 兼容。

**教训**：Docker for Mac 的 bind mount 限制是已知坑，文件级 mount 直接绕开。需要注入配置时优先考虑环境变量或目录 mount。

---

## 7. compose 产生 duplicate key

给 spark-master 加环境变量时，Edit 的 old_string 没覆盖到原有的 `environment:` block，导致新增了一个 `environment:`，compose 报 `DUPLICATE_KEY`。

**修复**：重写整份 docker-compose.yml，合并成一个 `environment:` block。

**教训**：Edit 的 old_string 要能唯一匹配。如果文件里已有相同的 key，要么一起改，要么用 Write 整体重写（短文件直接 Write 更安全）。

---

## 8. Health check 用 TCP 探测失败

计划里写的是：
```yaml
test: ["CMD-SHELL", "bash -c '</dev/tcp/127.0.0.1/7077'"]
```

Master 容器内 7077 端口实际没监听（Spark master 启动后 RPC 端口在不同条件下可能延迟），health check 连续失败，container 变成 unhealthy。

**修复**：改成 HTTP 探测 `curl -sf http://localhost:8080`，Web UI 启动后返回 200，health check 立刻通过。

**教训**：Spark master 的 7077 是 cluster 通信端口，不是健康检查的最佳探测点。Web UI (8080) 是更可靠的 ready 信号。如果要测 7077，应该从 worker 或宿主机角度测，不要从容器内部 localhost。

---

## 9. `await_args_list` 时序假设

timeout 测试里用 `create_process.await_args_list[1:]` 取 cleanup 命令，假设第一个 call 是主进程，后面是 cleanup。如果未来 runner 实现变了（比如加了前置检查），这个假设就错了。

**修复思路**：用 `call_args_list[-2:]` 取最后两个 cleanup 命令，不依赖前几个 call 的时序。

**教训**：测试 mock 的 call_args_list 时，按内容匹配（如 command 前缀）比按索引取更稳健。

---

## 总结

| # | 坑 | 类别 | 严重度 |
|---|---|---|---|
| 1 | 测试类名非 Test 开头 | 测试 | 阻塞 |
| 2 | 异步测试缺 IsolatedAsyncioTestCase | 测试 | 阻塞 |
| 3 | 漏 import unittest | 测试 | 阻塞 |
| 4 | unused import 累积 | 代码质量 | 低 |
| 5 | TOOL_DEFINITIONS tuple vs dict | 运行时 bug | **高** |
| 6 | Docker for Mac 文件 bind mount | 环境 | 阻塞 |
| 7 | compose duplicate key | 配置 | 阻塞 |
| 8 | TCP health check 不可靠 | 配置 | 中 |
| 9 | await_args_list 时序假设 | 测试脆弱性 | 低 |

最重要的两条：
- **#5 tuple vs dict**：工具列表是全局状态，一个元素的类型错误影响所有代码路径，应该在提交前跑全量测试（不只是新增文件）。
- **#6 Docker for Mac mount 限制**：文件级 bind mount 不支持是平台特性，不是配置错误，需要提前知道。
