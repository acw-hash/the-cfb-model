import styles from "./not-found.module.css";

/** Unknown game_id — rendered inside the root layout so banners and site chrome stay intact. */
export default function GameNotFound(): React.ReactElement {
  return (
    <main className={styles.page} data-testid="game-not-found">
      <h1 className={styles.title}>Game not found</h1>
      <p className={styles.body}>No published forecast matches this game.</p>
    </main>
  );
}
