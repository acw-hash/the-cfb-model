import type { SlateOrder } from "@/lib/this-week/sort";

import styles from "./SortControl.module.css";

interface SortControlProps {
  value: SlateOrder;
  onChange: (order: SlateOrder) => void;
}

const OPTIONS: { id: SlateOrder; label: string }[] = [
  { id: "kickoff", label: "By kickoff" },
  { id: "conviction", label: "By conviction" },
];

/** Visible slate ordering control — current state is always on the selected option. */
export function SortControl({ value, onChange }: SortControlProps): React.ReactElement {
  return (
    <div className={styles.bar}>
      <p className={styles.legend} id="slate-order-label">
        Order
      </p>
      <div
        className={styles.group}
        role="radiogroup"
        aria-labelledby="slate-order-label"
        data-testid="sort-control"
      >
        {OPTIONS.map((option) => {
          const selected = value === option.id;
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={selected}
              className={selected ? styles.selected : styles.option}
              data-testid={`sort-${option.id}`}
              onClick={() => onChange(option.id)}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
