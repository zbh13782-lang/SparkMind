# 踩坑记录

## 2026-08-11 Spark 集成

### 1. 测试类名必须 Test 开头

pytest 默认只发现 `Test*` 开头的类。写了 `class SparkJobModelTests`，所有测试方法全部跳过，pytest 输出 `no tests ran`。

**修复**：类名改成 `Test*` 开头。

**教训**：先跑一次 `pytest -q` 看 collected 数量，不要等写完全部测试才跑。

---

### 2. 异步测试必须继承 IsolatedAsyncioTestCase

`async def test_xxx` 写在普通 class 里，pytest 不会自动运行。

**修复**：异步测试继承 `unittest.IsolatedAsyncioTestCase`。

---

### 3. TOOL_DEFINITIONS 元素混用 tuple 和 dict

把 `run_spark_job` 的定义包了一层括号 `({...},)` 变成了 tuple，其他工具都是裸 dict。`_tools_overview` 对每个元素调 `.get("function", {})`，tuple 没有 `.get()`，直接抛 `AttributeError`。

**修复**：去掉外层括号，改成和其他工具一致的裸 dict。

**教训**：往列表里加新元素时，照抄现有元素的类型。工具注册是全局状态，写完立刻跑全量测试。

---

### 4. Docker for Mac 不支持文件级 bind mount

`docker-compose.yml` 写了 `./docker/spark-defaults.conf:/opt/spark/conf/spark-defaults.conf:ro`。Docker for Mac 通过 gRPC-FUSE 做 bind mount，不支持将单个文件 mount 到文件，启动 worker 时报错。

**修复**：去掉文件级 volume mount，改成环境变量注入。

**教训**：Docker for Mac 文件级 bind mount 不支持是平台特性，不是配置错误。需要注入配置时优先考虑环境变量或目录 mount。

---

### 5. compose duplicate key

Edit 的 old_string 没覆盖到原有的 `environment:` block，导致新增了一个 `environment:`，compose 报 `DUPLICATE_KEY`。

**修复**：重写整份 docker-compose.yml，合并成一个 `environment:` block。

**教训**：Edit 的 old_string 要能唯一匹配。如果文件里已有相同的 key，短文件直接 Write 整体重写更安全。

---

### 6. Health check 用 TCP 探测失败

计划里写 `bash -c '</dev/tcp/127.0.0.1/7077'` 探测 7077 端口。Master 容器内 7077 实际没监听（Spark master 启动后 RPC 端口可能延迟），health check 连续失败。

**修复**：改成 HTTP 探测 `curl -sf http://localhost:8080`，Web UI 启动后返回 200，立刻通过。

**教训**：Spark master 的 7077 是 cluster 通信端口，不是健康检查的最佳探测点。Web UI (8080) 是更可靠的 ready 信号。

---

### 7. `await_args_list` 时序假设

timeout 测试里用 `create_process.await_args_list[1:]` 取 cleanup 命令，假设第一个 call 是主进程，后面是 cleanup。未来 runner 实现变了，这个假设就错了。

**教训**：测试 mock 的 call_args_list 时，按内容匹配（如 command 前缀）比按索引取更稳健。

---

## 2026-08-11 Sandbox Code + Advisor Tools

### 8. `from __future__ import annotations` 与模块级 `@dataclass` 冲突

`runner.py` 加了 `from __future__ import annotations`，导致模块级 `@dataclass(frozen=True)` 装饰器报 `NameError: name 'dataclass' is not defined`。

**修复**：删除 `from __future__ import annotations`。

**教训**：`from __future__ import annotations` 把所有注解变成字符串，包括装饰器参数，会破坏 `@dataclass` 等运行时元编程。

---

### 9. 模块级单例在 import 时执行 `from_config()` 会崩溃

`_ADVISOR = AdvisorService.from_config()` 写在模块顶层，读取 YAML 时如果 `api_key` 为空字符串，`AdvisorConfig` 校验抛 `ValueError`，导致整个模块无法 import，所有测试全部失败。

**修复**：改为惰性初始化——`_ADVISOR: AdvisorService | None = None`，通过 `_get_advisor()` 函数在首次调用时创建实例。

**教训**：模块顶层不要执行可能失败的 I/O 或配置校验。配置相关的创建操作应该惰性化。

---

### 10. `asyncio.TimeoutError` 已废弃

`asyncio.timeout()` 上下文管理器抛出的是内置 `TimeoutError`，不是 `asyncio.TimeoutError`。IDE 报 deprecated 警告。

**修复**：`except TimeoutError:` 而不是 `except asyncio.TimeoutError:`。

---

### 11. 测试里返回值类型混淆

测试写 `config = self._make_service()`，但 `_make_service` 返回的是 `AdvisorService` 实例。后续 `config.model` 会 AttributeError。

**修复**：`config = self._make_service().config`。

**教训**：工厂方法返回什么类型要明确。如果返回的是包装对象，测试里要取 `.config` 属性。

---

### 12. `AsyncMock(return_value=X)` 对异步方法不生效

`fake = AsyncMock(return_value=expected)`，然后 `await fake.ask()` 不返回 expected。

**修复**：

```python
fake = AsyncMock()
fake.ask.return_value = expected
```

**教训**：`AsyncMock` 的 `return_value` 只在 mock 本身被 await 时生效。如果 mock 是一个对象（如 client），需要在具体方法上设 `return_value`。
