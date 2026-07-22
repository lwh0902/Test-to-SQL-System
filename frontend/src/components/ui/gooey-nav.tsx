import { useEffect, useRef, useState } from 'react';
import './gooey-nav.css';

export interface GooeyNavItem {
  label: string;
  value?: string;
  icon?: React.ReactNode;
}

export interface GooeyNavProps {
  items: GooeyNavItem[];
  initialActiveIndex?: number;
  particleCount?: number;
  particleDistances?: [number, number];
  particleR?: number;
  animationTime?: number;
  timeVariance?: number;
  colors?: number[];
  onSelect?: (item: GooeyNavItem, index: number) => void;
  orientation?: 'horizontal' | 'vertical';
}

const COLOR_VARS = [
  'var(--color-1)',
  'var(--color-2)',
  'var(--color-3)',
  'var(--color-4)',
];

function isTouchDevice() {
  return 'ontouchstart' in window || navigator.maxTouchPoints > 0;
}

function particleEffect(
  target: HTMLElement,
  colors: number[],
  particleCount: number,
  particleDistances: [number, number],
  particleR: number,
  timeVariance: number,
  animationTime: number,
) {
  if (!target) return;
  const parent = target.parentElement;
  if (!parent) return;

  const parentRect = parent.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  const x = targetRect.left - parentRect.left + targetRect.width / 2;
  const y = targetRect.top - parentRect.top + targetRect.height / 2;

  for (let i = 0; i < particleCount; i++) {
    const particle = document.createElement('span');
    particle.classList.add('particle');
    parent.appendChild(particle);

    const colorIdx = colors[i % colors.length];
    const color = COLOR_VARS[colorIdx - 1] ?? COLOR_VARS[0];
    particle.style.background = color;
    particle.style.display = 'block';
    particle.style.width = `${particleR / 12}px`;
    particle.style.height = `${particleR / 12}px`;

    const angle = (i / particleCount) * Math.PI * 2 + Math.random() * 0.5;
    const dist = particleDistances[0] * (0.6 + Math.random() * 0.6);
    const tx = Math.cos(angle) * dist;
    const ty = Math.sin(angle) * particleDistances[1] * (0.5 + Math.random());
    const rotation = Math.random() * 360;

    particle.style.left = `${x}px`;
    particle.style.top = `${y}px`;
    particle.style.transform = `translate(-50%, -50%) rotate(${rotation}deg)`;

    particle.animate(
      [
        { transform: `translate(-50%, -50%) translate(0, 0) rotate(${rotation}deg)`, opacity: 1 },
        {
          transform: `translate(-50%, -50%) translate(${tx}px, ${ty}px) rotate(${rotation + 180}deg)`,
          opacity: 0,
        },
      ],
      {
        duration: animationTime + Math.random() * timeVariance,
        easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
        fill: 'forwards',
      },
    ).onfinish = () => particle.remove();
  }
}

export default function GooeyNav({
  items,
  initialActiveIndex = 0,
  particleCount = 12,
  particleDistances = [70, 8],
  particleR = 80,
  animationTime = 500,
  timeVariance = 250,
  colors = [1, 2, 3, 1, 2, 4],
  onSelect,
  orientation = 'horizontal',
}: GooeyNavProps) {
  const safeInitial = Math.max(0, Math.min(initialActiveIndex, Math.max(0, items.length - 1)));
  const [activeIndex, setActiveIndex] = useState(safeInitial);
  const touchDevice = isTouchDevice();
  const hoverLockRef = useRef(false);

  useEffect(() => {
    if (initialActiveIndex >= 0 && initialActiveIndex < items.length) {
      setActiveIndex(initialActiveIndex);
    }
  }, [initialActiveIndex, items.length]);

  const emitParticles = (target: HTMLElement) => {
    if (hoverLockRef.current) return;
    hoverLockRef.current = true;
    window.setTimeout(() => { hoverLockRef.current = false; }, 200);
    particleEffect(
      target,
      colors,
      particleCount,
      particleDistances,
      particleR,
      timeVariance,
      animationTime,
    );
  };

  const handleClick = (idx: number, e: React.MouseEvent<HTMLButtonElement>) => {
    if (!touchDevice) emitParticles(e.currentTarget);
    setActiveIndex(idx);
    onSelect?.(items[idx], idx);
  };

  return (
    <div className={`gooey-nav-container${orientation === 'vertical' ? ' vertical' : ''}`}>
      <nav>
        <ul>
          {items.map((item, idx) => (
            <li key={`${item.value ?? item.label}-${idx}`} className={idx === activeIndex ? 'active' : ''}>
              <button
                type="button"
                onClick={(e) => handleClick(idx, e)}
                onMouseEnter={(e) => {
                  if (touchDevice) return;
                  emitParticles(e.currentTarget);
                }}
              >
                {item.icon && <span className="gooey-nav-icon">{item.icon}</span>}
                <span>{item.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
