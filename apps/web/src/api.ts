export async function fetchScene(sceneId: string) {
  const res = await fetch(`/api/scenes/${sceneId}`);
  if (!res.ok) throw new Error(`scene fetch failed: ${res.status}`);
  return res.json();
}
