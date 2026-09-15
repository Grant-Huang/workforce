// Turn Manager：Answerability + Progressive Response + GREETING + 质疑路径
// 复用现有 session.update.instructions + response.create，不平行复制 Realtime 协议。
const TurnManager = (() => {
  const GREETING_RE =
    /^(你好啊|你好呀|你好|您好|嗨|哈喽|在吗|在不在|早上好|晚安|hi|hello|hey)[\s！!。.~～啊呀吗嘛]*$/i;
  const CHALLENGE_RE = /(不对|不是这样|你再查|再查查|搞错了|记错了|错了吧|不是这个意思|你查错)/;

  let turnSeq = 0;
  let contextVersion = 0;
  /** @type {{turnId:string, version:number}|null} */
  let activeTurn = null;

  function nextTurnId() {
    turnSeq += 1;
    return `T${turnSeq}`;
  }

  function bumpVersion() {
    contextVersion += 1;
    return contextVersion;
  }

  function classify(text) {
    const t = (text || "").trim();
    if (!t) return { kind: "empty", confidence: 0 };
    if (CHALLENGE_RE.test(t)) return { kind: "challenge", confidence: 0.4 };
    if (GREETING_RE.test(t) || (t.length <= 8 && /^(你好|在吗|嗨)/.test(t))) {
      return { kind: "greeting", confidence: 0.95 };
    }
    if (SaveIntent.detect(t)) return { kind: "save_intent", confidence: 0.95 };
    // 明显需要外部时效信息
    if (/(天气|新闻|股价|最新|网上|搜一下)/.test(t)) {
      return { kind: "needs_retrieval", confidence: 0.35, scene: "retrieve_search" };
    }
    // 工单/备件/交接等细节默认不在 hot，走 AgentNexus Retrieval
    if (/(WO-?\d+|工单|备件|FAN-M102|交接|SKU-|李工|王强|安全库存)/i.test(t)) {
      return { kind: "needs_retrieval", confidence: 0.35, scene: "retrieve_record" };
    }
    return { kind: "general", confidence: 0.6 };
  }

  function answerability(text, workingDoc) {
    const cls = classify(text);
    if (cls.kind === "greeting" || cls.kind === "save_intent") {
      return { type: "A", confidence: cls.confidence, cls, localHits: [] };
    }
    if (cls.kind === "challenge") {
      return { type: "C", confidence: 0.3, cls, localHits: [], forceRetrieval: true };
    }
    if (cls.kind === "needs_retrieval") {
      return { type: "C", confidence: cls.confidence, cls, localHits: [], scene: cls.scene };
    }
    const localHits = WorkingMemory.searchHot(workingDoc, text, 5);
    // 本地命中足够 → 类型 A
    if (localHits.length >= 1 && localHits[0].text && localHits[0].text.length > 8) {
      const conf = Math.min(0.92, 0.7 + localHits.length * 0.05);
      if (conf >= 0.85) return { type: "A", confidence: conf, cls, localHits };
      return { type: "B", confidence: conf, cls, localHits };
    }
    // 画像相关问题也可本地答
    const profile = workingDoc.user_profile || {};
    if (/(兴趣|角色|负责|排产|设备)/.test(text) && (profile.role || (profile.interests || []).length)) {
      return { type: "A", confidence: 0.88, cls, localHits: [], useProfile: true };
    }
    if (cls.confidence >= 0.85) return { type: "A", confidence: cls.confidence, cls, localHits };
    if (cls.confidence >= 0.5) return { type: "B", confidence: cls.confidence, cls, localHits };
    return { type: "C", confidence: cls.confidence, cls, localHits, scene: "retrieve_generic" };
  }

  function buildGreetingGuidance(profile, workingDoc) {
    const interests = profile.interests || [];
    const role = profile.role || "";
    const topics = profile.preferred_topics || [];
    const summary = workingDoc.conversation_summary || {};
    const summaryTopics = summary.topics || [];
    const note = (summary.note || "").trim();
    const hotTexts = (workingDoc.hot_memory || [])
      .map((e) => (e && e.text) || "")
      .filter(Boolean);
    const hook =
      hotTexts.find((t) => /M102|温度|停机|排产|OEE|告警/.test(t)) ||
      note ||
      summaryTopics[0] ||
      topics[0] ||
      "";
    const episodic = summaryTopics[0] || topics[0] || "";
    const main = interests[0] || episodic || "";
    const alt = interests[1] || topics[1] || episodic || "";

    if (main) {
      let line = `你好。要不要先聊聊${role ? role + "关心的" : ""}${main}`;
      if (episodic && episodic !== main) line += `——比如${episodic}`;
      if (alt && alt !== main && alt !== episodic) line += `，还是${alt}`;
      line += "？";
      if (hook) {
        const shortHook = hook.length > 48 ? `${hook.slice(0, 48)}…` : hook;
        line += `（可顺带提一句已知背景：${shortHook}）`;
      }
      return line;
    }
    return "你好。今天想先聊工作上的事，还是随便聊聊？";
  }

  function formatContextBlock(results, { forWeb = false } = {}) {
    const lines = [];
    for (const r of results || []) {
      if (r.provider === "websearch" || r.source === "websearch") {
        if (!r.citation || !r.citation.url) continue; // 分层 C
        lines.push(`- [WebSearch] ${r.text}（来源：${r.citation.title || ""} ${r.citation.url}）`);
      } else {
        lines.push(`- [Memory/${r.source || "memory"}] ${r.text}`);
      }
    }
    if (!lines.length && forWeb) {
      return "公开检索未找到带可靠来源的结果；请明确告知用户未查到可靠来源，不要编造网页事实。";
    }
    return lines.join("\n");
  }

  /**
   * 主入口：替换原 handleUserTurn 内的检索+答路径。
   * @param {object} deps
   * @param {string} deps.text
   * @param {object} deps.session — voiceSession / textSession
   * @param {string} deps.baseInstructions
   * @param {function} deps.sendEventOn
   * @param {function} deps.markTurnTiming
   * @param {object} deps.voiceSession
   */
  async function handleTurn(deps) {
    const {
      text,
      session,
      baseInstructions,
      sendEventOn,
      markTurnTiming,
      voiceSession,
    } = deps;

    const user = Auth.user();
    if (!user || !user.user_id) {
      throw new Error("未登录");
    }

    const turnId = nextTurnId();
    const version = bumpVersion();
    activeTurn = { turnId, version };

    const workingDoc = await WorkingMemory.get(user.user_id);
    const evalResult = answerability(text, workingDoc);

    // --- save-intent：走 memory/event + 短确认 ---
    if (evalResult.cls.kind === "save_intent") {
      const saveIntent = SaveIntent.detect(text);
      const localEntry = LocalMemory.add(saveIntent.content, { layer: "PROGRESS" });
      try {
        const data = await ContextClient.memoryEvent({
          type: "upsert",
          text: saveIntent.content,
          layer: "PROGRESS",
          provenance: "save_intent",
        });
        if (localEntry && data.entry) {
          LocalMemory.markSynced(localEntry.id, { source: "local", sourceId: data.entry.id });
          await WorkingMemory.upsertHotMemory(user.user_id, {
            id: data.entry.id,
            text: saveIntent.content,
            source: "local",
          });
        }
        if (data.agentnexus && localEntry) {
          LocalMemory.markSynced(localEntry.id, {
            source: "agentnexus",
            sourceId: data.agentnexus.entry_id,
          });
        }
      } catch (e) {
        console.warn("memory/event failed:", e);
      }
      const instructions = `${baseInstructions}\n\n用户刚才明确要求记住这件事："${saveIntent.content}"，你已经帮TA记下了。只需要简短确认一句就行，不要复述内容、不要追问。`;
      await session.updater.updateInstructionsAndWait(instructions);
      if (session === voiceSession) markTurnTiming("sessionUpdatedAckAt");
      sendEventOn(session.getWs(), { type: "response.create" });
      if (session === voiceSession) markTurnTiming("responseCreateSentAt");
      session.responsePending = true;
      return { turnId, type: "A", path: "save_intent" };
    }

    // --- GREETING：画像引导，跳过 Retrieval ---
    if (evalResult.cls.kind === "greeting") {
      const guidance = buildGreetingGuidance(workingDoc.user_profile || user.profile || {}, workingDoc);
      const instructions = `${baseInstructions}\n\n本轮是寒暄开场。请按下面话术自然说（可轻微口语化，但不要另起清单或臆测兴趣）：\n${guidance}`;
      await session.updater.updateInstructionsAndWait(instructions);
      if (session === voiceSession) markTurnTiming("sessionUpdatedAckAt");
      sendEventOn(session.getWs(), { type: "response.create" });
      if (session === voiceSession) markTurnTiming("responseCreateSentAt");
      session.responsePending = true;
      return { turnId, type: "A", path: "greeting" };
    }

    // --- 类型 A：本地可答，跳过 Retrieval/Refined ---
    if (evalResult.type === "A" && !evalResult.forceRetrieval) {
      const lines = (evalResult.localHits || []).map((e) => `- ${e.text}`);
      const profile = workingDoc.user_profile || {};
      let extra = "";
      if (lines.length) {
        extra = `\n\n以下是本地 Working Memory 中可能相关的内容（可无 citation）：\n${lines.join("\n")}`;
      } else if (evalResult.useProfile) {
        extra = `\n\n用户画像：角色=${profile.role || "未知"}；兴趣=${(profile.interests || []).join("、") || "无"}；偏好话题=${(profile.preferred_topics || []).join("、") || "无"}。只依据这些回答，不要臆测。`;
      }
      await session.updater.updateInstructionsAndWait(baseInstructions + extra);
      if (session === voiceSession) markTurnTiming("sessionUpdatedAckAt");
      sendEventOn(session.getWs(), { type: "response.create" });
      if (session === voiceSession) markTurnTiming("responseCreateSentAt");
      session.responsePending = true;
      return { turnId, type: "A", path: "local", skippedRetrieval: true };
    }

    // --- 类型 B / C：Immediate + 并发 Retrieval → Refined ---
    const scene =
      evalResult.scene ||
      (evalResult.cls.kind === "challenge" ? "retrieve_record" : "retrieve_generic");
    const phrase = TransitionPhrases.pick(evalResult.type === "B" ? "partial_bridge" : scene);

    const myVersion = version;
    const latencyBudget = 1500;
    const providers =
      evalResult.cls.kind === "challenge" || evalResult.forceRetrieval
        ? ["memory", "websearch"]
        : undefined;

    // 先发起检索（AgentNexus mock 通常 <100ms），与过渡语并行
    const queryPromise = ContextClient.queryContext({
      session_id: `S-${user.user_id}`,
      turn_id: turnId,
      context_version: myVersion,
      query: text,
      options: {
        latency_budget_ms: latencyBudget,
        max_results: 10,
        providers,
      },
    }).catch((e) => {
      console.warn("context/query failed:", e);
      return null;
    });

    // 快查直出：本地 Memory 很快有结果时，跳过过渡语 + 取消/再播，避免双次 session.update 卡住
    const FAST_MS = 400;
    const raced = await Promise.race([
      queryPromise.then((data) => ({ kind: "query", data })),
      new Promise((resolve) => setTimeout(() => resolve({ kind: "slow" }), FAST_MS)),
    ]);

    if (!activeTurn || activeTurn.version !== myVersion) {
      return { turnId, type: evalResult.type, path: "discarded" };
    }

    const hasUsable =
      raced.kind === "query" &&
      raced.data &&
      !raced.data.timed_out &&
      (raced.data.results || []).some((r) => r && r.text);

    if (hasUsable) {
      const ctxBlock = formatContextBlock(raced.data.results);
      const oneShot = `${baseInstructions}\n\n检索已完成（来自 Memory/AgentNexus）。请直接口语简短回答，依据下列 Context；不要编造。WebSearch 条目必须有来源才可当事实。\n${ctxBlock}`;
      await session.updater.updateInstructionsAndWait(oneShot, 2500);
      if (session === voiceSession) markTurnTiming("sessionUpdatedAckAt");
      sendEventOn(session.getWs(), { type: "response.create" });
      if (session === voiceSession) markTurnTiming("responseCreateSentAt");
      session.responsePending = true;
      return { turnId, type: evalResult.type, path: "fast_memory", queryData: raced.data };
    }

    // 慢路径：先播过渡语，再等检索 → Refined
    let immediateExtra = "";
    if (evalResult.type === "B" && (evalResult.localHits || []).length) {
      const lines = evalResult.localHits.map((e) => `- ${e.text}`);
      immediateExtra = `\n\n你目前只能依据下面已知内容做简短 Partial 回答，并说明还在确认：\n${lines.join("\n")}\n先说一句过渡：${phrase}`;
    } else {
      immediateExtra = `\n\n本轮需要检索。请只说下面这句过渡语（不要添加任何业务事实）：\n${phrase}`;
    }
    await session.updater.updateInstructionsAndWait(baseInstructions + immediateExtra, 2500);
    if (session === voiceSession) markTurnTiming("sessionUpdatedAckAt");
    sendEventOn(session.getWs(), { type: "response.create" });
    if (session === voiceSession) markTurnTiming("responseCreateSentAt");
    session.responsePending = true;

    let queryData = await queryPromise;
    let timedOut = !queryData || !!queryData.timed_out;

    if (!activeTurn || activeTurn.version !== myVersion) {
      return { turnId, type: evalResult.type, path: "discarded" };
    }

    if (timedOut) {
      console.info("检索超时：取消 Refined，仅保留 Immediate（后续优化项）");
      return { turnId, type: evalResult.type, path: "timeout_immediate_only", timedOut: true };
    }

    // Immediate 已出声时稍等再播 Refined；快查慢路径也不要干等 1.2s
    await new Promise((r) => setTimeout(r, 450));
    if (!activeTurn || activeTurn.version !== myVersion) {
      return { turnId, type: evalResult.type, path: "discarded_before_refined" };
    }

    const ctxBlock = formatContextBlock(queryData && queryData.results);
    const refined = `${baseInstructions}\n\n检索已完成。请基于下列 Context 给出更准确的补充或更正（口语、短说）。WebSearch 条目必须有来源才可当事实；Memory/Profile 可无 citation。\n${ctxBlock || "（无额外结果：如实说明未查到。）"}`;

    if (session.responsePending) {
      sendEventOn(session.getWs(), { type: "response.cancel" });
      session.responsePending = false;
      if (session === voiceSession && typeof stopPlayback === "function") {
        try {
          stopPlayback();
        } catch (_) {}
      }
    }
    await session.updater.updateInstructionsAndWait(refined, 2500);
    sendEventOn(session.getWs(), { type: "response.create" });
    session.responsePending = true;

    // 质疑回写
    if (evalResult.cls.kind === "challenge" && queryData && (queryData.results || []).length) {
      const top = queryData.results.find((r) => r.provider === "memory") || queryData.results[0];
      if (top && top.text) {
        try {
          const saved = await ContextClient.memoryEvent({
            type: "upsert",
            text: top.text,
            provenance: "challenge_refine",
          });
          if (saved.entry) {
            await WorkingMemory.upsertHotMemory(user.user_id, {
              id: saved.entry.id,
              text: saved.entry.text,
              source: saved.entry.source,
            });
          }
        } catch (e) {
          console.warn("challenge rewrite failed:", e);
        }
      }
    }

    return { turnId, type: evalResult.type, path: "refined", queryData };
  }

  function invalidate() {
    bumpVersion();
  }

  return { handleTurn, classify, answerability, buildGreetingGuidance, invalidate };
})();
