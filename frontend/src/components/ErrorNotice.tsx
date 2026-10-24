export function ErrorNotice({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="rounded-xl border border-red-300/70 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-400/30 dark:bg-red-500/10 dark:text-red-200"
    >
      {message}
    </div>
  );
}
