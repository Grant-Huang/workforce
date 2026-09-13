# PR — pipecat 1.9 Settings API 兼容修复

**仓库**：`Grant-Huang/workforce`
**分支**：`main`
**改动文件**：
- `web-demo/pipecat-local-agent/local_services/stt_sensevoice.py`（+8/-1）
- `web-demo/pipecat-local-agent/local_services/tts_qwen_local.py`（+10/-2）
**影响**：`pipecat-ai >= 1.9.0` 必需；不影响 pipecat 1.8.x 用户

---

## 背景

升级 `pipecat-ai` 到 1.9.0 之后,部署在 `https://workforce.inkpath.cc/` 的 voice pipeline 在浏览器连接时崩溃。崩溃点不在网络层、不在 LiveKit Cloud 凭证、不在 Cloudflare Tunnel 路由 —— 而在本地 pipeline 的 STT 和 TTS 节点初始化。

崩溃堆栈（两个不同端点触发,同一个根因）：

```
# 触发 1: 用户开 /?mode=livekit → /api/livekit/token → bot 拉起
File "web-demo/pipecat-local-agent/local_services/factory.py", line 25, in build_stt
    return SenseVoiceSTTService(config)
File "web-demo/pipecat-local-agent/local_services/stt_sensevoice.py", line 25, in __init__
    super().__init__(settings=self.Settings(model=config.stt_model), **kwargs)
AttributeError: 'SenseVoiceSTTService' object has no attribute 'Settings'

# 触发 2: bot_manager 走 compare 模式加载 TTS
File "web-demo/pipecat-local-agent/local_services/factory.py", line 39, in build_tts
    return QwenLocalTTSService(config)
File "web-demo/pipecat-local-agent/local_services/tts_qwen_local.py", line 29, in __init__
    settings=self.Settings(model=config.tts_model, voice=config.tts_voice, language=None)
AttributeError: 'QwenLocalTTSService' object has no attribute 'Settings'
```

触发链路：

1. 用户在公网打开 `https://workforce.inkpath.cc/?mode=livekit`
2. 前端 `livekit-app.js` 调 `/api/livekit/token`
3. `unified_server.py::livekit_token()` 走 compare 模式 → 调 `bot_manager.ensure_started(room)`
4. `bot_manager` 子进程跑 `pipecat_agent.py` → 加载 `factory.build_stt(config)` + `factory.build_tts(config)` → 两个都崩
5. token API 返回 HTTP 503，前端拿不到 token，无法加入 LiveKit Cloud 房间

---

## 根因

`pipecat-ai 1.9.0` 重构了 AI service 的 settings 模型：

| 版本 | settings 类型 | 位置 | 怎么传 |
|---|---|---|---|
| `< 1.9` | `MyService.Settings`（每个 service 类**内嵌**的 dataclass） | 每个 service 自己定义 | `super().__init__(settings=self.Settings(model=...), **kwargs)` |
| `>= 1.9` | `STTSettings` / `LLMSettings` / `TTSSettings`（顶层 dataclass） | `pipecat.services.settings` | `super().__init__(settings=STTSettings(model=..., language=...), **kwargs)` |

`pipecat-ai` 在 1.9 的 changelog 没特别标这个迁移，但 `STTService.Settings` 和 `TTSService.Settings` 这两个**类属性**被彻底移除了。`AIService.__init__` 仍然收 `settings: ServiceSettings | None = None` 关键字参数，签名没变，所以 `super().__init__(settings=..., **kwargs)` 模式本身没坏 —— 坏的是 `self.Settings` 这个**实例**引用（因为类属性 `Settings` 不再存在，`self.Settings` 走 `__getattr__` 时 AttributeError）。

### 关键诊断步骤（同型 bug 通用）

```python
# 1) 看基类 __init__ 的 settings 参数类型
from pipecat.services.ai_service import AIService
import inspect
sig = inspect.signature(AIService.__init__)
# 参数 `settings: ServiceSettings | None = None`  ← 签名没变

# 2) 找正确的 settings 类
from pipecat.services.settings import STTSettings, LLMSettings, TTSSettings
for f in STTSettings.__dataclass_fields__:
    print(f)  # 'model', 'language', 'extra', '_aliases'

# 3) 老代码里 `self.Settings(...)` 在新版本不存在,grep 同型 bug:
#    grep -rn "self\.Settings(" web-demo/  → 这次有 2 处: stt_sensevoice.py + tts_qwen_local.py

# 4) 区分:类属性引用 `MyClass.Settings(...)` 在 1.9 仍兼容
from pipecat.services.openai.llm import OpenAILLMService
hasattr(OpenAILLMService, 'Settings')  # True — LLM 不需要改
```

---

## 修复

### `stt_sensevoice.py`

```diff
 from pipecat.frames.frames import ErrorFrame, TranscriptionFrame
+from pipecat.services.settings import STTSettings
 from pipecat.services.stt_service import SegmentedSTTService
 from pipecat.utils.time import time_now_iso8601
 from pipecat.utils.tracing.service_decorators import traced_stt

 from .config import LocalAgentConfig


 class SenseVoiceSTTService(SegmentedSTTService):
     """Segmented STT using FunASR SenseVoice on local GPU (MPS/CUDA/CPU)."""

     def __init__(self, config: LocalAgentConfig, **kwargs: Any) -> None:
-        super().__init__(settings=self.Settings(model=config.stt_model), **kwargs)
+        # pipecat 1.9: settings moved to pipecat.services.settings.STTSettings.
+        # Earlier (<1.9) this used `self.Settings(model=...)` (a per-class inner
+        # dataclass on SegmentedSTTService); that attribute no longer exists in 1.9.
+        super().__init__(
+            settings=STTSettings(model=config.stt_model, language=config.stt_language),
+            **kwargs,
+        )
         self._config = config
         self._model = None
```

### `tts_qwen_local.py`

```diff
 from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame
+from pipecat.services.settings import TTSSettings
 from pipecat.services.tts_service import TTSService
 from pipecat.utils.tracing.service_decorators import traced_tts

 from .config import LocalAgentConfig


 class QwenLocalTTSService(TTSService):
     """Local Qwen3-TTS synthesis on Mac (MPS/CUDA/CPU)."""

     def __init__(self, config: LocalAgentConfig, **kwargs: Any) -> None:
+        # pipecat 1.9: settings moved to pipecat.services.settings.TTSSettings.
+        # Earlier (<1.9) this used `self.Settings(...)` (a per-class inner dataclass
+        # on TTSService); that attribute no longer exists in 1.9.
         super().__init__(
             sample_rate=config.tts_sample_rate,
             push_start_frame=True,
             push_stop_frames=True,
-            settings=self.Settings(model=config.tts_model, voice=config.tts_voice, language=None),
+            settings=TTSSettings(model=config.tts_model, voice=config.tts_voice, language=None),
             **kwargs,
         )
         self._config = config
         self._runtime = None
```

### 为什么 LLM (`llm_local.py`) 不用改

```python
# llm_local.py 里用的是类属性引用,不是 self.Settings:
settings=OpenAILLMService.Settings(...)   # ← 类属性,1.9 仍兼容
settings=QwenLLMService.Settings(...)     # ← 类属性,1.9 仍兼容

# 验证:
python3 -c "
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.qwen.llm import QwenLLMService
print('OpenAILLMService.Settings:', hasattr(OpenAILLMService, 'Settings'))  # True
print('QwenLLMService.Settings:  ', hasattr(QwenLLMService, 'Settings'))    # True
"
```

`OpenAILLMService.Settings` 和 `QwenLLMService.Settings` 在 1.9 里是**类属性**，访问路径走 `__class__` 而不是 `self`，所以即使 1.9 移除了实例层的 `self.Settings`，类属性这条路径仍然工作。

### 改动要点

- STT: 多带一个 `language=config.stt_language` —— 新 `STTSettings` 的字段比老 `Settings` 多了 `language`，正好用上 `LocalAgentConfig.stt_language`（默认 `auto`）
- TTS: `voice=None` 改成 `language=None`（新 `TTSSettings` 字段），其他不变

---

## 验证

### 单元层面（无 LiveKit Cloud、无 bot 子进程）

```bash
cd ~/work/projects/workforce/web-demo/pipecat-livekit
.venv/bin/python -c "
import sys; sys.path.insert(0, '../pipecat-local-agent')
from local_services.stt_sensevoice import SenseVoiceSTTService
from local_services.tts_qwen_local import QwenLocalTTSService
from local_services.config import LocalAgentConfig
cfg = LocalAgentConfig.from_env()
svc_stt = SenseVoiceSTTService(cfg)
svc_tts = QwenLocalTTSService(cfg)
print('STT:', svc_stt._settings)  # STTSettings(model='iic/SenseVoiceSmall', language='auto')
print('TTS:', svc_tts._settings)  # TTSSettings(model='Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice', voice='Cherry')
"
```

### 集成层面（端到端 smoke test）

```bash
# 1. unified server 在跑
lsof -nP -iTCP:8788 -sTCP:LISTEN

# 2. /api/agent/wake 拉起 bot 子进程不再崩溃
curl -s "https://workforce.inkpath.cc/api/agent/wake?room=voicechat-compare"
# {"status":"success","data":{"status":"starting","room":"voicechat-compare","pid":N,"kind":"agent"}}

# 3. /api/livekit/token 走 compare 模式也走通(触发完整 STT+TTS+LLM 加载)
curl -s "https://workforce.inkpath.cc/api/livekit/token?room=voicechat-compare&participant=smoke"
# {"status":"success","data":{"token":"eyJhbGc...","url":"wss://nian-j64dt7qv.livekit.cloud", ...}}

# 4. /api/agent/status 确认 running
curl -s https://workforce.inkpath.cc/api/agent/status
# {"status":"success","data":{"status":"running","room":"voicechat-compare","pid":N,"kind":"agent"}}
```

### 回归

- `pipecat-ai 1.8.x` 用户：不受影响（`STTSettings` / `TTSSettings` 在 1.8 已经在 `pipecat.services.settings`，所以 import 兼容）
- `pipecat-ai >= 1.9` 用户：必须合这个 PR，否则 `/api/livekit/token` 在 compare 模式下永远 503，浏览器无法加入 LiveKit Cloud 房间

---

## 同型 bug 巡检（建议合并前跑）

`web-demo/` 整个仓库里如果有其他自定义 STT/LLM/TTS service 用了 `self.Settings(...)` 模式，1.9 升级都会炸。建议 PR 合并前跑一次：

```bash
cd ~/work/projects/workforce
grep -rn "self\.Settings(" web-demo/ 2>/dev/null
# 期望:空（PR 合并后）
```

如果还搜到，按同样模式替换：`self.Settings(model=...)` → `STTSettings(model=..., language=...)` / `TTSSettings(model=..., voice=..., language=...)` / `LLMSettings(...)`，import 从 `pipecat.services.settings` 加进来。

`MyClass.Settings(...)` 这种类属性引用**不需要改**，但建议跑一下确认还在：

```bash
grep -rn "\.Settings(" web-demo/ 2>/dev/null
# 期望只剩 OpenAILLMService.Settings / QwenLLMService.Settings 这两条
# 验证它们都还能用:看 §"为什么 LLM 不用改"
```

---

## 历史与变更

| 日期 | 改动 | 原因 |
|---|---|---|
| 2026-09-12 21:5x | 修 `stt_sensevoice.py` | 第一次发现 bug,STT 在 compare 模式 token API 时崩 |
| 2026-09-12 22:0x | 修 `tts_qwen_local.py` | unified server 上线后 token API 仍 503,深挖发现 TTS 也有同一 bug |

PR 第一次提交只覆盖 STT,unified server 上线后才发现 TTS 同样的 bug,本 PR 把两文件合并提交。

---

## 关联

- 部署任务：把 `workforce` 部署到 `workforce.inkpath.cc` 单域名 unified server（`unified_server.py`，端口 8788）
- 架构决策：见 [`docs/unified-server-architecture.md`](./unified-server-architecture.md)
- 本地化部署与测试手册：[`docs/local-deployment-and-test-runbook.md`](./local-deployment-and-test-runbook.md)
- LaunchAgent：`~/Library/LaunchAgents/ai.workforce.unified.plist`
- 关联 PR 之前的文档：本文件原名 `pr-sttsettings-pipecat-1.9-compat.md`，本次扩到 TTS
