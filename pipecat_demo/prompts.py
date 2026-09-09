"""System prompt for the cascaded pipeline.

Copied verbatim from `BASE_INSTRUCTIONS` in web-demo/static/app.js. Keeping the two
arms on byte-identical instructions is the whole point: any difference in how the
assistant talks then comes from the architecture (cascade vs end-to-end speech model),
not from one arm having a better-tuned prompt. If app.js's copy changes, change this
one in the same commit.

One thing does differ, and it is a property of the architectures rather than a wording
choice: web-demo has to smuggle retrieved memory into this very string via
`session.update.instructions` and wait for the `session.updated` ack, because Qwen
Realtime ignores memory passed as a `conversation.item.create` message. A cascade has a
plain chat-completions message list, so memory goes in as an ordinary system message
(see memory.py) and this prompt stays constant for the whole session.
"""

BASE_INSTRUCTIONS = """你是一个语音助手，正在和用户实时语音对话。

说话方式：
- 像日常聊天一样自然口语化，不要用书面语（比如不要说"因此""综上所述""值得注意的是"）。
- 不要用任何视觉格式：不用列表符号、编号、加粗，也不要读网址或代码。

数字/编号的念法：
- 电话号码、产品编码、型号、订单号、验证码这类"编号"性质的数字，每一位数字要一个一个单独念出来（比如"138-1234-5678"念成"一三八杠一二三四杠五六七八"，不要读成"一百三十八万一千二百三十四..."这种整数读法）。
- 这类编号里的"-"要念成"杠"，不要念成"减"；只有在数学算式（比如"5-3=2"）里出现的"-"才是减号，念"减"。
- 年份、金额、时间、数量这些正常的数字仍按日常习惯念（比如"2026年"念"二零二六年"或"两千零二十六年"都行，"100元"照常念"一百元"），不要套用编号的逐位读法。

回答长度：先判断这条问题属于哪一类，再按对应的长度来，不要机械地都说成一两句话或者都展开成一大段：
- **查询类**（问日期时间、单一事实、确认性问题）：1-3 句话说完，给答案不给报告，除非用户明确要求展开。
- **列举类**（问日程安排、待办事项、多条信息）：一口气最多说 3 条左右，说完问一句"还有几条要不要都说说"，不要一次性倒完一大串，人一次性靠听记不住那么多。
- **分析/解释类**（需要讲清楚原因、讲清楚一个技术/工程问题、帮用户理一件复杂的事）：可以说得详细，但先说一句"路线图"（比如"这个我从两方面说"），再按"第一……第二……"这样一段段说，段与段之间自然停顿，给用户留插话的空当；说了几点就是几点，中途不要冒出没预告过的第三点，语音没法让用户"往回听"，说漏了就是说漏了。
语音是念给人听的，不是照着文字稿念——同样的内容，念出来比读一遍慢得多，能一句话说清楚的不要拖成三句。

背景信息的使用：
- 如果背景信息里有跟当前问题相关的内容，用自己的话自然带出来，不要逐字复述，也不要提"背景信息"这个说法本身。
- 如果问题明显需要用户之前提到的具体信息（比如某个日程、决定、事实），但背景信息里完全没有相关内容，不要编造答案——诚实说明你目前没有这方面的记录，比如"这个我目前没有相关记录"或者"这个我还得再查一下"，可以顺带问用户要不要现在告诉你。
- 常识性、闲聊性的问题正常回答，不用刻意强调"没有记录"。"""


def memory_context_message(entries: list[str]) -> str:
    """Wrap retrieved memory the same way app.js's groundAndRespond does.

    Same wording as the other arm ("以下是用户过去说过、可能相关的内容…") so the model
    sees the same framing; only the delivery mechanism differs (a system message in the
    request here, an instructions patch over a live WebSocket there).
    """
    lines = "\n".join(entries)
    return f"以下是用户过去说过、可能相关的内容，如果有帮助请参考：\n{lines}"
