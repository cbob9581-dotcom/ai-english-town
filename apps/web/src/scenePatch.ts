import type { Entity } from './types';

export interface PatchOp {
  op: 'add' | 'replace' | 'remove';
  path: string;
  entity?: Entity;
  value?: unknown;
}

/** 白名单 patch 应用：只允许 /entities/<id> 与 /setting；未知 path 忽略（安全）。 */
export function applyScenePatch(
  entities: Entity[],
  setting: unknown,
  ops: PatchOp[],
): { entities: Entity[]; setting: any } {
  const out = [...entities];
  let nextSetting = setting;
  for (const op of ops) {
    if (op.path.startsWith('/entities/')) {
      const id = op.path.slice('/entities/'.length);
      const i = out.findIndex((e) => e.id === id);
      if (op.op === 'add' || op.op === 'replace') {
        if (!op.entity) continue;
        if (i >= 0) out[i] = op.entity; else out.push(op.entity);
      } else if (op.op === 'remove' && i >= 0) {
        out.splice(i, 1);
      }
    } else if (op.path === '/setting' && op.op === 'replace') {
      nextSetting = op.value;
    }
  }
  return { entities: out, setting: nextSetting };
}
