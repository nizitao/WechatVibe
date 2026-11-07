// Share recent invitation evidence and ambiguous courtesy cues between fine intent candidates and grounding.
const QUOTED = /“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"]*"|`[^`]*`/gu;
const INVITATION = /(?:要不要|能不能|可以|想不想|愿不愿意).{0,12}(?:一起|出来|见面|聚聚)|(?:一起|约你|约我|约个|请你|请我).{0,12}(?:吃饭|吃个饭|喝|看电影|看展|见面|出去|出来|逛|玩|打球)|一起.{0,6}(?:去|来)|(?:出来|见面|聚聚|吃个饭|看电影|看展)(?:吗|么|不|吧|[？?])/u;
const CASUAL_ARRANGEMENT = /^(?:那|咱们|我们)?(?:今晚|晚上|周末|明晚)(?:怎么|咋)(?:说|安排)[？?。！!\s]*$/u;

export function recentIntentContext(context: string): string {
  return Array.from(String(context || "").replace(QUOTED, " ").trim()).slice(-240).join("");
}

/** Calendar words alone do not establish that this conversation contains an invitation. */
export function hasInvitationContext(context: string): boolean {
  const turns = recentIntentContext(context).split(/\n/u).map((line) => line.trim()).filter(Boolean).slice(-3);
  return turns.some((line) => !/^(?:他说|她说|有人说|引用|比如|例如|假设|如果|假如)/u.test(line)
    && (INVITATION.test(line) || CASUAL_ARRANGEMENT.test(line)));
}

export function isContextualCourtesy(target: string, context: string): boolean {
  return !!recentIntentContext(context) && /^(?:(?:好(?:的)?|行|收到|嗯+)[，,。！!\s]*)?(?:谢谢|感谢|多谢|谢了|辛苦(?:你|您)?了)/u.test(target);
}
