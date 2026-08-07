/** 双端过期丢弃（前端侧）：非当前 turnId 的 delta/commit/metadata/tts.audio.start 一律丢弃。
 * currentTurnId 为 null（新一轮尚未建立，beginUtterance/语音触发后）→ 接受首个非空 turnId，
 * 用于建立当前回合（RULING 1：reset 后 "the next accepted message then becomes the new current turn"）。 */
export function isAcceptedTurn(
  currentTurnId: string | null,
  companionTurnId: string | null,
  turnId: string | null | undefined,
): boolean {
  if (!turnId) return false;
  if (currentTurnId === null) return true;
  return turnId === currentTurnId || turnId === companionTurnId;
}
