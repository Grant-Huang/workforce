// 过渡语库（与服务端 transition_phrases.py 对齐，前端可本地抽选）。
const TransitionPhrases = (() => {
  const PHRASES = {
    retrieve_generic: [
      "我查一下。", "嗯，我确认一下。", "稍等，我看一眼。", "我先核实一下。",
      "好，我这边查一下。", "让我确认一下。", "我马上看一下。", "稍等片刻。",
    ],
    retrieve_record: [
      "我看一下记录。", "我翻一下相关记录。", "让我查下这条记录。", "我核对一下记录。",
      "稍等，我看记录。", "我去对一下记录。", "记录这边我查一下。",
    ],
    retrieve_search: [
      "我搜一下最新的公开信息。", "我查一下网上最新说法。", "稍等，我搜一下。",
      "我去查一下公开来源。", "让我检索一下最新信息。", "我搜一下相关公开资料。", "稍等，我查最新消息。",
    ],
    retrieve_memory: [
      "我翻一下之前的笔记。", "我回忆一下你提过的。", "让我看看以前记的。",
      "我查一下你之前说过的。", "稍等，我翻翻记忆。", "我对照一下之前记下的。", "笔记这边我看一下。",
    ],
    partial_bridge: [
      "我先说我这边已有的，再确认最新数据。", "先给你已知部分，我同时再核实一下。",
      "我先讲手头有的，马上再核对。", "已知的先说，细节我再确认。",
      "部分信息我有了，其余我查一下。", "先给你一个初步说法，我再核对。", "我先按已知情况说，同时再确认。",
    ],
  };

  function pick(scene) {
    const bucket = PHRASES[scene] || PHRASES.retrieve_generic;
    return bucket[Math.floor(Math.random() * bucket.length)];
  }

  return { pick, PHRASES };
})();
