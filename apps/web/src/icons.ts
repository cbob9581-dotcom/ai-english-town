// 注意：从 apps/web/src/ 到仓库根 assets/ 是 ../../../
import iconMap from '../../../assets/icons/icon-map.json';

export function renderIcon(visualKey: string): string {
  const entry = (iconMap as Record<string, { emoji: string; label: string }>)[visualKey];
  return entry?.emoji ?? '❓';
}
