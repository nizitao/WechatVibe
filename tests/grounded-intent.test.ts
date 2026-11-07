import assert from "node:assert/strict";
import { it } from "node:test";

import { groundedIntent, groundedIntentWithContext, isEmbeddedQuestionStatement, type GroundedIntentLabel, type IntentEvidenceKind } from "../electron/laya/grounded-intent";

const positive: Array<[string, GroundedIntentLabel, IntentEvidenceKind]> = [
  ["早上好！", "greet", "greeting_phrase"],
  ["你好，小王。", "greet", "greeting_phrase"],
  ["谢谢你帮我检查。", "thank", "thanks_phrase"],
  ["谢谢你，帮了大忙。", "thank", "thanks_phrase"],
  ["好的，谢谢你了。", "thank", "thanks_phrase"],
  ["嗯呢。谢谢啊。", "thank", "thanks_phrase"],
  ["谢谢大家。", "thank", "thanks_phrase"],
  ["感谢大家的支持。", "thank", "thanks_phrase"],
  ["辛苦大家了。", "thank", "thanks_phrase"],
  ["好了", "confirm", "short_acknowledgement"],
  ["明白了。", "confirm", "short_acknowledgement"],
  ["收到啦", "confirm", "short_acknowledgement"],
  ["我看看", "inspect", "first_person_inspection"],
  ["我先看一下。", "inspect", "first_person_inspection"],
  ["没问题。", "agree", "explicit_acceptance"],
  ["我同意。", "agree", "explicit_acceptance"],
  ["嗯嗯", "agree", "explicit_acceptance"],
  ["这个可以。", "agree", "explicit_acceptance"],
  ["这样可以", "agree", "explicit_acceptance"],
  ["这个可以哦。", "agree", "explicit_acceptance"],
  ["我不去。", "reject", "explicit_refusal"],
  ["这次不参加了。", "reject", "explicit_refusal"],
  ["谢谢，不过我不参加。", "reject", "explicit_refusal"],
  ["周末要不要一起看电影？", "invite", "inclusive_invitation"],
  ["周六要不要一起去看展？", "invite", "inclusive_invitation"],
  ["下星期三要不要一起吃饭？", "invite", "inclusive_invitation"],
  ["咱们一起去吃饭吧。", "invite", "inclusive_invitation"],
  ["这个参数怎么设置？", "ask_question", "answer_seeking_question"],
  ["这个可以吗？", "ask_question", "answer_seeking_question"],
  ["是旧版还是新版啊", "ask_question", "answer_seeking_question"],
  ["配置文件在哪里", "ask_question", "answer_seeking_question"],
  ["好了吗？", "ask_question", "answer_seeking_question"],
  ["你们几个人", "ask_question", "answer_seeking_question"],
  ["他家几楼", "ask_question", "answer_seeking_question"],
  ["能不能帮我看一下日志？", "seek_help", "action_request"],
  ["请把文件发给我。", "seek_help", "action_request"],
  ["建议先备份配置。", "suggest_action", "advice_marker"],
  ["那就先备份一下。", "suggest_action", "advice_marker"],
  ["那就备份一下。", "suggest_action", "advice_marker"],
  ["那你先检查一下。", "suggest_action", "advice_marker"],
  ["配置大一点。", "suggest_action", "imperative_adjustment"],
  ["字体调大点。", "suggest_action", "imperative_adjustment"],
  ["让小王检查一下。", "suggest_action", "delegated_action"],
  ["别再改配置了。", "suggest_action", "negative_imperative"],
  ["不要删原文件。", "suggest_action", "negative_imperative"],
  ["我打算下周整理文档。", "plan", "first_person_intention"],
  ["我先试试。", "plan", "first_person_intention"],
  ["我先把日志保存一下。", "plan", "first_person_intention"],
  ["我们明天准备部署新版本。", "plan", "first_person_intention"],
  ["不对，版本号是 1.0.1。", "correct", "explicit_correction"],
  ["应该不是这个版本。", "correct", "explicit_correction"],
  ["qq_123456:\n应该不是这个版本。", "correct", "explicit_correction"],
  ["你说的“旧版本”不对，当前是新版。", "correct", "explicit_correction"],
  ["因为接口超时，所以请求失败。", "explain", "causal_explanation"],
  ["原因是服务器断开连接。", "explain", "causal_explanation"],
  ["我这算是一步一步引导CODX帮我破解了", "explain", "process_explanation"],
  ["我相当于逐步指导他完成了。", "explain", "process_explanation"],
  ["怎么又卡住了？", "complain", "negative_evaluation"],
  ["真无语，等了半天还没好。", "complain", "negative_evaluation"],
  ["文件已上传到共享盘。", "status_report", "progress_statement"],
  ["修好了。", "status_report", "progress_statement"],
  ["搞定了。", "status_report", "progress_statement"],
  ["部署完了。", "status_report", "progress_statement"],
  ["已经搞定了。", "status_report", "progress_statement"],
  ["还在处理中。", "status_report", "progress_statement"],
  ["刚刚跑通了。", "status_report", "progress_statement"],
  ["任务还没完成。", "status_report", "progress_statement"],
  ["告诉你个好消息，考试过了。", "share_news", "sharing_announcement"],
  ["我今天考过了。", "share_news", "sharing_announcement"],
  ["你好，今天几点开会？", "ask_question", "answer_seeking_question"],
  ["你要我怎么称呼你", "ask_question", "answer_seeking_question"],
];

const abstain = [
  "",
  "喊老板",
  "喊姐姐",
  "喊妈妈",
  "别人都喊我老板。",
  "“谢谢。”",
  "他说：“谢谢。”",
  "我不是在邀请你。",
  "假设我们明天一起去。",
  "不是说好了吗？",
  "难道你不知道吗？",
  "我什么时候说我同意了？",
  "？？",
  "我这不把文件发过来了吗？",
  "我想问一下。",
  "我准备好了。",
  "他好了。",
  "确认。",
  "我看看原因。",
  "我这算是一步一步修好了。",
  "我先试过了。",
  "我先试了。",
  "那就先这样。",
  "那就这样。",
  "房间大一点。",
  "他让我检查一下。",
  "让他检查了一下。",
  "是旧版还是新版的区别。",
  "别以为我同意了。",
  "别急。",
  "他打算下周离开。",
  "这个词叫“感谢”。",
  "我知道怎么修复这个问题。",
  "这说明为什么会失败。",
  "他解释了为什么没来。",
  "好的，谢谢你，我这就过去。",
  "好的，电话已经发给你了。",
  "这也太好找了。",
  "这些都是不错的选择呢。",
  "给你推荐几个地方吧。",
  "没什么。",
  "这个颜色不合适，我换一件。",
  "我还有几件。",
  "我买了几本。",
  "库存还有几件。",
];

for (const [message, label, evidenceKind] of positive) {
  it(`grounds ${JSON.stringify(message)}`, () => {
    assert.deepEqual(groundedIntent(message), { label, evidenceKind });
  });
}

for (const message of abstain) {
  it(`abstains on ${JSON.stringify(message)}`, () => {
    assert.equal(groundedIntent(message), null);
  });
}

it("uses invitation context to ground a deferral without changing target-only rules", () => {
  assert.deepEqual(groundedIntentWithContext("改天吧", "周六一起去看展吗？"),
    { label: "reject", evidenceKind: "contextual_deferral" });
  assert.equal(groundedIntentWithContext("改天吧", "今天忙吗？"), null);
  assert.deepEqual(groundedIntentWithContext("木有", "你是不是生气了？"),
    { label: "deny", evidenceKind: "contextual_denial" });
  assert.equal(groundedIntentWithContext("木有", "今天吃什么？"), null);
});

it("calendar or work scheduling words do not establish an invitation", () => {
  for (const context of ["客户下周才看。", "明天交的文档，今天必须改完吗？",
    "会议安排发给你了。", "今天忙吗？", "他说周六一起去看展。"])
    assert.equal(groundedIntentWithContext("改天[笑脸]", context), null, context);
  for (const context of ["今晚一起吃个饭？", "晚上咋说？", "明天一起去吗？"])
    assert.deepEqual(groundedIntentWithContext("改天吧", context),
      { label: "reject", evidenceKind: "contextual_deferral" }, context);
});

it("an explicit second-person help request is not a first-person inspection", () => {
  for (const message of ["你帮我看看。", "您替我查一下。", "这个太复杂，你帮我检查一下吧。"])
    assert.deepEqual(groundedIntent(message), { label: "seek_help", evidenceKind: "action_request" }, message);
  for (const message of ["我帮你看看。", "你帮我看过了。", "昨天你帮我看看就好了。",
    "他说你帮我看看。", "如果你帮我看看，我再决定。"])
    assert.notEqual(groundedIntent(message)?.label, "seek_help", message);
});

it("contextual courtesy remains a model judgment while isolated thanks keep literal grounding", () => {
  for (const context of ["药给你放门口了。", "我把你准备的惊喜告诉他了。"])
    assert.equal(groundedIntentWithContext("谢谢你啊。", context), null, context);
  assert.deepEqual(groundedIntentWithContext("谢谢你啊。", ""),
    { label: "thank", evidenceKind: "thanks_phrase" });
  assert.deepEqual(groundedIntentWithContext("谢谢，不过我不去。", "今晚一起吃饭？"),
    { label: "reject", evidenceKind: "explicit_refusal" });
});

it("grounds first-person corrections without mistaking a qualified denial for refusal", () => {
  for (const message of ["我没有说不去，只是需要晚一点。", "我没说不参加，只是要晚到十分钟。",
    "我不是生你的气，是觉得沟通有点累。", "我们并不是不愿意，而是时间不合适。"])
    assert.deepEqual(groundedIntent(message), {label:"correct", evidenceKind:"explicit_correction"});
  for (const message of ['他说：“我没有说不去，只是晚一点。”', "比如我没有说不去，只是晚一点。",
    "如果我不是生你的气，是因为别的事呢？", "我并不是没有参加。", "让我再想想，我暂时没有把握。"])
    assert.equal(groundedIntent(message), null, message);
});

it("grounds a complete negative answer only against the latest explicit proposition", () => {
  const reply = "不是，我也很喜欢和熟悉的人一起出去。";
  assert.deepEqual(groundedIntentWithContext(reply, "所以你总是喜欢一个人吗？"),
    {label:"deny", evidenceKind:"contextual_denial"});
  for (const context of ["今天吃什么？", "你喜欢独处吗？\n我们几点出发？", "他说你喜欢独处吗？",
    "我正在研究是否调整方案。", "小王说你是不是喜欢独处", "听说你喜欢独处吗？", ""])
    assert.equal(groundedIntentWithContext(reply, context), null, context);
  assert.equal(groundedIntentWithContext('他说：“不是，我喜欢出去。”', "你喜欢独处吗？"), null);
  assert.deepEqual(groundedIntentWithContext("我没有说改天，只是需要晚一点。", "明天一起去吗？"),
    {label:"correct", evidenceKind:"explicit_correction"});
});

it("grounds actual reschedules and chat pauses, including transparent framing", () => {
  for (const message of ["下午不太方便，我们改到周日上午吧。", "我们把会议改到下周一吧。",
    "我的想法是：时间改到明天下午吧。", "改到周六上午吧。", "有效，我没有取消，只是把时间改到了明天。"])
    assert.deepEqual(groundedIntent(message), {label:"plan",evidenceKind:"concrete_reschedule"}, message);
  for (const message of ["我有点累了，想先休息，明天再接着说。", "先不聊了，明天再聊。",
    "我先休息一下，晚点再聊。", "我的想法是：今天就聊到这里。"])
    assert.deepEqual(groundedIntent(message), {label:"close_chat",evidenceKind:"conversation_pause"}, message);
  for (const message of ["我没有把时间改到周五。", "时间没有改到明天。", "把字段改成明天。",
    "会议改到明天的方案我不同意。", "如果会议改到周一，我就去。", "小王说，我们改到周二。",
    "小王把会议改到周二了。", "他说我先休息一下，明天再聊。", "我先休息一下。", "我们没有说先不聊了。"])
    assert.equal(groundedIntent(message), null, message);
});

it("an action request after its reason still outranks a question or courtesy", () => {
  for (const message of ["有点多，可以帮我分担最后的整理吗？", "这个工具我弄不明白，能不能帮我看下？",
    "我的想法是：给我一瓶无糖的，谢谢！", "你能不能帮我备份一下？"])
    assert.deepEqual(groundedIntent(message), {label:"seek_help",evidenceKind:"action_request"}, message);
  for (const message of ["小王问我，能不能帮我修一下。", "她说，给我一瓶无糖的。",
    "我说的不是请你帮我修。", "没有让你帮我改。", '比如“请你帮我备份”。'])
    assert.equal(groundedIntent(message), null, message);
});

it("preserves a condition of acceptance instead of reducing it to routine agreement", () => {
  for (const message of ["愿意，只要提前确认交通和返程时间。", "可以，前提是把时间确认好。"])
    assert.deepEqual(groundedIntent(message), {label:"agree",evidenceKind:"conditional_acceptance"}, message);
  for (const message of ["不愿意，只要先确认时间。", "他说愿意，只要提前确认。", "如果可以，前提是时间合适。"])
    assert.equal(groundedIntent(message), null, message);
});

it("advice and commitments survive narrative framing without grounding past actions", () => {
  for (const message of ["先列出必须完成的事情，其余可以留到下次。",
    "我的想法是：先列出必须完成的事情，其余可以留到下次。",
    "暂时还说不准，最好问清楚，别替他下结论。", "可以，大家把想法写下来，我们再一起比较。"])
    assert.deepEqual(groundedIntent(message), {label:"suggest_action",evidenceKind:"advice_marker"}, message);
  for (const message of ["有一点，不过具体建议很有用，我会认真改。", "我负责查路线，晚点发安排。"])
    assert.deepEqual(groundedIntent(message), {label:"plan",evidenceKind:"first_person_intention"}, message);
  for (const message of ["小王说，先列出必须完成的事情。", "他建议，最好问清楚。",
    "我昨天负责查路线。", "我已经改完了。"])
    assert.notEqual(groundedIntent(message)?.label, "plan", message);
});

it("embedded questions stay statements while actual requests for answers remain available", () => {
  for (const message of ["我在意实际能不能完成，也想听听大家的感受。", "我的想法是：我在意实际能不能完成。",
    "我已经知道怎么备份。", "我们正在研究是否调整方案。"])
    assert.equal(isEmbeddedQuestionStatement(message), true, message);
  for (const message of ["你知道怎么备份吗？", "能不能帮我备份？", "这个周末有什么安排？", "我想知道怎么备份。"])
    assert.equal(isEmbeddedQuestionStatement(message), false, message);
});

it("keeps the author's own action after a report without adopting an unquoted colon citation", () => {
  for (const message of ["他说会议改到周三，但我建议先确认地点。", "小王说，会议改到周三。我建议先确认地点。",
    '他说：“会议改到周三。”我建议先确认地点。'])
    assert.deepEqual(groundedIntent(message), {label:"suggest_action",evidenceKind:"advice_marker"}, message);
  assert.deepEqual(groundedIntent("他说我做得不够，不过我会认真改。"), {label:"plan",evidenceKind:"first_person_intention"});
  for (const message of ["小王问我，我会不会帮他。", "他说：会议改到周三，但我建议先确认地点。"])
    assert.equal(groundedIntent(message), null, message);
});
