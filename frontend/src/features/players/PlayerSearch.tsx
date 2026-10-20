import { useId, useState, type FormEvent } from "react";
import { useRouter } from "../../shared/router/context";
import { parseRiotId, routeToPath } from "../../shared/router/routes";

/** Busca de jogador pelo Riot ID no topo (CC-1102), como em sites de estatísticas. */
export function PlayerSearch() {
  const { navigate } = useRouter();
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);
  const hintId = useId();

  function submit(event: FormEvent) {
    event.preventDefault();
    const riotId = parseRiotId(value);
    if (!riotId) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setValue("");
    navigate(routeToPath({ name: "player", ...riotId }));
  }

  return (
    <form className="player-search" role="search" aria-label="Buscar jogador" onSubmit={submit} noValidate>
      <label className="sr-only" htmlFor={`${hintId}-input`}>Buscar jogador pelo Riot ID</label>
      <input
        id={`${hintId}-input`}
        type="search"
        value={value}
        placeholder="Nome#TAG"
        autoComplete="off"
        spellCheck={false}
        aria-invalid={invalid}
        aria-describedby={invalid ? hintId : undefined}
        onChange={(event) => {
          setValue(event.target.value);
          if (invalid) setInvalid(false);
        }}
      />
      <button type="submit" className="button button--secondary">Buscar</button>
      {invalid ? (
        <p id={hintId} className="player-search__hint" role="alert">
          Use o formato Nome#TAG (a TAG tem de 3 a 5 letras ou números).
        </p>
      ) : null}
    </form>
  );
}
