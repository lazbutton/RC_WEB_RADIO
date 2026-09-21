import { forwardRef } from "react";
import { Icon } from "../../components/Icon";
import { Kbd } from "../../components/Kbd";

type Props = {
  value: string;
  onChange: (value: string) => void;
  meta: string;
  busy?: boolean;
};

export const SearchBox = forwardRef<HTMLInputElement, Props>(function SearchBox({ value, onChange, meta, busy }, ref) {
  return (
    <label className={`mail-search${value ? " has-value" : ""}`}>
      <span className="sr-only">Rechercher dans tous les mails</span>
      <Icon name="search" className="search-icon" />
      <input
        ref={ref}
        type="search"
        value={value}
        onChange={(ev) => onChange(ev.target.value)}
        placeholder="Chercher dans tous les mails"
        autoComplete="off"
        spellCheck={false}
        enterKeyHint="search"
        aria-busy={busy}
      />
      {value ? (
        <button type="button" className="search-clear" onClick={() => onChange("")} aria-label="Effacer la recherche">
          <Icon name="close" size={12} />
        </button>
      ) : (
        <Kbd>/</Kbd>
      )}
      <span className="search-meta" aria-live="polite">
        {busy ? <span className="spinner" aria-hidden /> : meta}
      </span>
    </label>
  );
});
