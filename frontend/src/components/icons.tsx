// A handful of stroke icons drawn on one 20px grid, so they share weight and
// size. Decorative: callers give the surrounding control its accessible name.

type IconProps = { className?: string };

function Icon({ className = "h-4 w-4", children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 20 20"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {children}
    </svg>
  );
}

export const ChevronLeft = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12.5 4.5L7 10l5.5 5.5" />
  </Icon>
);

export const ChevronRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7.5 4.5L13 10l-5.5 5.5" />
  </Icon>
);

export const ArrowLeft = (p: IconProps) => (
  <Icon {...p}>
    <path d="M16 10H4M8.5 5.5L4 10l4.5 4.5" />
  </Icon>
);

export const Check = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4.5 10.5l3.5 3.5 7.5-8" />
  </Icon>
);

export const Close = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 5l10 10M15 5L5 15" />
  </Icon>
);

export const Menu = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3.5 6h13M3.5 10h13M3.5 14h13" />
  </Icon>
);

export const Bell = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10 3a4.5 4.5 0 00-4.5 4.5v2.4c0 .8-.3 1.6-.8 2.2L3.5 13.5h13l-1.2-1.4a3.4 3.4 0 01-.8-2.2V7.5A4.5 4.5 0 0010 3z" />
    <path d="M8.2 16a1.9 1.9 0 003.6 0" />
  </Icon>
);

export const Sun = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="10" cy="10" r="3.2" />
    <path d="M10 2.5v1.7M10 15.8v1.7M2.5 10h1.7M15.8 10h1.7M4.7 4.7l1.2 1.2M14.1 14.1l1.2 1.2M4.7 15.3l1.2-1.2M14.1 5.9l1.2-1.2" />
  </Icon>
);

export const Moon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M16 12.2A6.5 6.5 0 017.8 4a6.5 6.5 0 108.2 8.2z" />
  </Icon>
);

export const MapPin = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10 17.5s5.5-4.9 5.5-9.4a5.5 5.5 0 00-11 0c0 4.5 5.5 9.4 5.5 9.4z" />
    <circle cx="10" cy="8" r="2" />
  </Icon>
);

export const ExternalLink = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8.5 4.5h-4v11h11v-4M12 4.5h3.5V8M15.5 4.5L9 11" />
  </Icon>
);

// Admin console sections.
export const Grid = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3.5" y="3.5" width="5" height="5" rx="1" />
    <rect x="11.5" y="3.5" width="5" height="5" rx="1" />
    <rect x="3.5" y="11.5" width="5" height="5" rx="1" />
    <rect x="11.5" y="11.5" width="5" height="5" rx="1" />
  </Icon>
);

export const Calendar = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="4.5" width="14" height="12" rx="2" />
    <path d="M3 8.5h14M7 2.5v4M13 2.5v4" />
  </Icon>
);

export const List = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7.5 5.5h9M7.5 10h9M7.5 14.5h9M3.5 5.5h.01M3.5 10h.01M3.5 14.5h.01" />
  </Icon>
);

export const Warning = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10 3.5l7 12.5H3L10 3.5z" />
    <path d="M10 8.5v3M10 14h.01" />
  </Icon>
);

export const Person = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="10" cy="7" r="3" />
    <path d="M4 16.5c1-3 3.3-4.5 6-4.5s5 1.5 6 4.5" />
  </Icon>
);

export const People = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="7.5" cy="7" r="2.5" />
    <path d="M2.5 16c.8-2.6 2.6-4 5-4s4.2 1.4 5 4M13 4.6a2.5 2.5 0 010 4.8M14.5 12.2c1.4.5 2.4 1.8 3 3.8" />
  </Icon>
);

export const Tag = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3.5 3.5h6l7 7-6 6-7-7v-6z" />
    <path d="M7 7h.01" />
  </Icon>
);

export const Clock = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="10" cy="10" r="6.5" />
    <path d="M10 6.5V10l2.5 1.5" />
  </Icon>
);

export const Sliders = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 6h7M15 6h1M4 14h1M9 14h7" />
    <circle cx="13" cy="6" r="2" />
    <circle cx="7" cy="14" r="2" />
  </Icon>
);

export const FileText = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5.5 2.5h6l3.5 3.5v11.5h-9.5z" />
    <path d="M11.5 2.5V6H15M8 10h4.5M8 13.5h4.5" />
  </Icon>
);

export const SignOut = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 4.5H4.5v11H8M12 6.5l3.5 3.5-3.5 3.5M15.5 10H8" />
  </Icon>
);

/** The app's mark: a calendar page with one slot taken. */
export function Mark({ className = "h-5 w-5" }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" className={className} aria-hidden>
      <rect x="2" y="3" width="16" height="15" rx="3.5" className="fill-accent" />
      <rect x="5.5" y="6" width="9" height="1.6" rx="0.8" className="fill-canvas" opacity="0.5" />
      <rect x="5.5" y="10" width="4" height="4" rx="1" className="fill-canvas" />
    </svg>
  );
}
