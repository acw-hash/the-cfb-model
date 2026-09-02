import styles from "./TeamSearch.module.css";

interface TeamSearchProps {
  value: string;
  onChange: (value: string) => void;
  onClear: () => void;
}

/** Single-line team-name filter for the This Week slate. */
export function TeamSearch({ value, onChange, onClear }: TeamSearchProps): React.ReactElement {
  const showClear = value.length > 0;

  return (
    <div className={styles.wrap}>
      <label className={styles.label} htmlFor="team-search">
        Search teams
      </label>
      <input
        id="team-search"
        type="search"
        className={styles.input}
        placeholder="Search teams"
        value={value}
        data-testid="team-search"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClear();
          }
        }}
      />
      {showClear ? (
        <button
          type="button"
          className={styles.clear}
          aria-label="Clear search"
          data-testid="team-search-clear"
          onClick={onClear}
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
