/** 双端过期丢弃（前端侧）：非当前 turnId 的 delta/commit/metadata/tts.audio.start 一律丢弃。 */
export function isAcceptedTurn(
  currentTurnId: string | null,
  companionTurnId: string | null,
  turnId: string | null | undefined,
): boolean {
  if (!turnId) return false;
  if (currentTurnId === null) return true;
  return turnId === currentTurnId || turnId === companionTurnId;
}

/** 门 1（对话）：speech delta/commit、metadata、tts.audio.* = genId **且** turnId。 */
export function acceptTurnMessage(
  currentGenId: string | null,
  currentTurnId: string | null,
  companionTurnId: string | null,
  msgGenId: string | null | undefined,
  msgTurnId: string | null | undefined,
): boolean {
  if (!msgTurnId || !msgGenId) return false;
  if (currentGenId !== msgGenId) return false; // 跨场景晚到 → 丢
  return isAcceptedTurn(currentTurnId, companionTurnId, msgTurnId);
}

/** 门 2（场景）：scene.patch = genId + sceneId + baseRevision。 */
export function acceptSceneMessage(
  currentGenId: string | null,
  currentSceneId: string | null,
  currentRevision: number | null,
  msgGenId: string | null | undefined,
  msgSceneId: string | null | undefined,
  baseRevision: number | undefined,
): boolean {
  if (!msgGenId || !msgSceneId) return false;
  if (currentGenId !== msgGenId || currentSceneId !== msgSceneId) return false;
  return baseRevision === undefined || baseRevision === currentRevision;
}
