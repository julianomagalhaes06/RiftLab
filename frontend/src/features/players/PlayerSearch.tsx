import { useId, useState, type FocusEvent, type FormEvent, type KeyboardEvent } from "react";
import { useRouter } from "../../shared/router/context";
import { parseRiotId, routeToPath } from "../../shared/router/routes";
import { emblemSrc, profileIconUrl, rankTitle } from "../ranked/tiers";
import {
  clearRecentPlayers,
  matchRecentPlayers,
  readRecentPlayers,
  tagSuggestion,
  type RecentPlayer,
} from "./recentPlayers";

type Option =
  | { kind: "recent"; player: RecentPlayer }
  | { kind: "tag"; gameName: string; tagLine: string };

function RecentPortrait({ player }: { player: RecentPlayer }) {
  const [broken, setBroken] = useState(false);
  const src = profileIconUrl(player);
  return (
    <span className="portrait player-search__portrait" aria-hidden="true">
      {src && !broken ? <img src={src} alt="" width="32" height="32" onError={() => setBroken(true)} /> : <span>{player.gameName.slice(0, 1).toUpperCase()}</span>}
    </span>
  );
}

/** Busca de jogador pelo Riot ID no topo (CC-1102), com sugestões (CC-1208): perfis já abertos
 * neste navegador cujo Nome#TAG contém o texto e, sem "#", o mesmo nome com a TAG BR1. Padrão
 * combobox: setas escolhem, Enter abre, Esc fecha; sem opção ativa, Enter busca o texto.
 * `variant="hero"`: a busca grande do início. */
export function PlayerSearch({ variant = "bar" }: { variant?: "bar" | "hero" }) {
  const { navigate } = useRouter();
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [recent, setRecent] = useState<RecentPlayer[]>([]);
  const hintId = useId();
  const listId = `${hintId}-list`;

  const matches = matchRecentPlayers(value, recent);
  const tag = tagSuggestion(value);
  const options: Option[] = [
    ...matches.map((player) => ({ kind: "recent" as const, player })),
    ...(tag && !matches.some((p) => `${p.gameName}#${p.tagLine}`.toLocaleLowerCase("pt-BR") === `${tag.gameName}#${tag.tagLine}`.toLocaleLowerCase("pt-BR"))
      ? [{ kind: "tag" as const, ...tag }]
      : []),
  ];
  const expanded = open && !invalid && options.length > 0;

  function show() {
    setRecent(readRecentPlayers());
    setOpen(true);
  }

  function go(riotId: { gameName: string; tagLine: string }) {
    setInvalid(false);
    setValue("");
    setOpen(false);
    setActive(-1);
    navigate(routeToPath({ name: "player", ...riotId }));
  }

  function choose(option: Option) {
    go(option.kind === "recent" ? { gameName: option.player.gameName, tagLine: option.player.tagLine } : option);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (expanded && active >= 0 && options[active]) {
      choose(options[active]);
      return;
    }
    const riotId = parseRiotId(value);
    if (!riotId) {
      setInvalid(true);
      setOpen(false);
      return;
    }
    go(riotId);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!expanded) {
        show();
        setActive(event.key === "ArrowDown" ? 0 : -1);
        return;
      }
      // Ciclo entre as opções e o texto digitado (-1): depois da última volta ao texto.
      const step = event.key === "ArrowDown" ? 1 : -1;
      const slots = options.length + 1;
      setActive((current) => ((current + 1 + step + slots) % slots) - 1);
    } else if (event.key === "Escape" && expanded) {
      event.preventDefault();
      setOpen(false);
      setActive(-1);
    }
  }

  function onBlur(event: FocusEvent<HTMLFormElement>) {
    // Fecha só quando o foco sai da busca inteira (campo, botão e "Limpar").
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setOpen(false);
      setActive(-1);
    }
  }

  const optionId = (index: number) => `${listId}-${index}`;
  return (
    <form className={variant === "hero" ? "player-search player-search--hero" : "player-search"} role="search" aria-label="Buscar jogador" onSubmit={submit} onBlur={onBlur} noValidate>
      <label className="sr-only" htmlFor={`${hintId}-input`}>Buscar jogador pelo Riot ID</label>
      <input
        id={`${hintId}-input`}
        type="search"
        role="combobox"
        value={value}
        placeholder={variant === "hero" ? "Nome#TAG do jogador" : "Buscar Nome#TAG"}
        autoComplete="off"
        spellCheck={false}
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-activedescendant={expanded && active >= 0 && active < options.length ? optionId(active) : undefined}
        aria-invalid={invalid}
        aria-describedby={invalid ? hintId : undefined}
        onFocus={show}
        onKeyDown={onKeyDown}
        onChange={(event) => {
          setValue(event.target.value);
          setActive(-1);
          if (!open) show();
          if (invalid) setInvalid(false);
        }}
      />
      <button type="submit" className="player-search__submit" aria-label="Buscar">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
        </svg>
      </button>
      <div className="player-search__popup surface" hidden={!expanded}>
        {matches.length ? <p className="player-search__group" aria-hidden="true">Buscas recentes</p> : null}
        <ul id={listId} role="listbox" aria-label="Sugestões de jogadores" className="player-search__options">
          {options.map((option, index) => (
            <li
              key={option.kind === "recent" ? `r-${option.player.gameName}#${option.player.tagLine}` : "tag"}
              id={optionId(index)}
              role="option"
              aria-selected={index === active}
              className={index === active ? "player-search__option is-active" : "player-search__option"}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(option)}
            >
              {option.kind === "recent" ? (
                <>
                  <RecentPortrait player={option.player} />
                  <span className="player-search__text">
                    <span className="player-search__name">{option.player.gameName} <span className="player-search__tag">#{option.player.tagLine}</span></span>
                    <span className="player-search__meta">
                      {option.player.summonerLevel != null ? `Nível ${option.player.summonerLevel}` : "Nível não visto"}
                      {option.player.rank ? (
                        <>
                          {" · "}
                          <span className="player-search__rank" data-tier={option.player.rank.tier}>
                            <img src={emblemSrc(option.player.rank.tier)} alt="" width="14" height="14" />
                            {rankTitle(option.player.rank.tier, option.player.rank.division)}
                          </span>
                        </>
                      ) : null}
                    </span>
                  </span>
                </>
              ) : (
                <>
                  <span className="player-search__glyph" aria-hidden="true">#</span>
                  <span className="player-search__text">
                    <span className="player-search__name">Buscar {option.gameName} <span className="player-search__tag">#{option.tagLine}</span></span>
                    <span className="player-search__meta">TAG mais comum no servidor BR</span>
                  </span>
                </>
              )}
            </li>
          ))}
        </ul>
        {matches.length ? (
          <div className="player-search__foot">
            <span>Guardadas só neste navegador.</span>
            <button
              type="button"
              className="player-search__clear"
              onClick={() => {
                clearRecentPlayers();
                setRecent([]);
                setActive(-1);
                document.getElementById(`${hintId}-input`)?.focus();
              }}
            >
              Limpar buscas recentes
            </button>
          </div>
        ) : null}
      </div>
      {invalid ? (
        <p id={hintId} className="player-search__hint" role="alert">
          Use o formato Nome#TAG (a TAG tem de 3 a 5 letras ou números).
        </p>
      ) : null}
    </form>
  );
}
