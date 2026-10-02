import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { titleCase } from "../lib/format";
import {
  STAT_LABELS,
  UNOWN_FORMS,
  UNOWN_ID,
  moveMethodsText,
  slugLabel,
  unownSymbol,
} from "../lib/dex";
import { Meter, Sprite } from "./Primitives";
import type {
  DexEntryView,
  DexSpeciesView,
  IndividualView,
  PokemonDetailView,
  StatView,
  UnownFormView,
} from "../types";

type Load =
  | { key: string; status: "ok"; data: PokemonDetailView }
  | { key: string; status: "error" };

const GENDER_LABELS: Record<string, string> = {
  male: "Male",
  female: "Female",
  genderless: "Genderless",
};

function numericDate(iso: string, lang: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const opts: Intl.DateTimeFormatOptions = { year: "numeric", month: "numeric", day: "numeric" };
  try {
    return d.toLocaleDateString(lang || undefined, opts);
  } catch {
    return d.toLocaleDateString(undefined, opts);
  }
}

function individualLabel(ind: IndividualView, n: number, lang: string): string {
  const when = ind.caught_at
    ? numericDate(ind.caught_at, lang)
    : ind.is_active
      ? "Raising"
      : "—";
  return `#${n} · ${ind.is_shiny ? "✨ Shiny" : "Normal"} · Lv. ${ind.level} · ${when}`;
}

export function PokemonDetail({
  species,
  unownForms,
  entries,
  language,
  onBack,
}: {
  species: DexSpeciesView;
  unownForms: UnownFormView[];
  /** The catch log, used to guess the appearance before the individuals arrive. */
  entries: DexEntryView[];
  language: string;
  onBack: () => void;
}) {
  const isUnown = species.species_id === UNOWN_ID;
  const [selectedForm, setSelectedForm] = useState<string | null>(null);
  const [selectedShiny, setSelectedShiny] = useState<boolean | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [load, setLoad] = useState<Load | null>(null);

  const loadKey = `${species.species_id}|${selectedForm ?? ""}|${attempt}`;
  useEffect(() => {
    let cancelled = false;
    api.pokemon(species.species_id, selectedForm).then(
      (data) => !cancelled && setLoad({ key: loadKey, status: "ok", data }),
      () => !cancelled && setLoad({ key: loadKey, status: "error" }),
    );
    return () => {
      cancelled = true;
    };
  }, [loadKey, species.species_id, selectedForm]);

  const current = load && load.key === loadKey ? load : null;
  const data = current?.status === "ok" ? current.data : null;

  const formView = selectedForm ? unownForms.find((f) => f.form === selectedForm) : undefined;
  const ownsShiny = formView ? formView.is_shiny : species.is_shiny;
  const ownsNormal = formView ? formView.has_normal : species.has_normal;

  const all = data?.individuals ?? [];
  const guess = entries.find(
    (e) =>
      e.final_id === species.species_id && (!selectedForm || e.unown_form === selectedForm),
  );
  const displayedShiny =
    selectedShiny !== null && (selectedShiny ? ownsShiny : ownsNormal)
      ? selectedShiny
      : data
        ? (all[0]?.is_shiny ?? ownsShiny)
        : (guess?.is_shiny ?? ownsShiny);
  const individuals = all.filter((i) => i.is_shiny === displayedShiny);
  const individual = individuals.find((i) => i.id === selectedId) ?? individuals[0] ?? null;

  const spriteForm = isUnown
    ? (selectedForm ?? individual?.unown_form ?? guess?.unown_form ?? unownForms[0]?.form ?? null)
    : null;
  const symbol = isUnown ? unownSymbol(spriteForm) : null;
  const name = symbol ? `${species.name} [${symbol}]` : species.name;
  const rarity = data?.rarity ?? species.rarity;

  const chooseForm = (form: string) => {
    setSelectedForm(selectedForm === form ? null : form);
    setSelectedShiny(null);
    setSelectedId(null);
  };

  return (
    <section className="panel pd">
      <div className="pd__bar">
        <button type="button" className="pd__back" onClick={onBack}>
          ‹ Back
        </button>
        <span className="pd__num">#{species.species_id}</span>
      </div>

      {ownsNormal && ownsShiny && (
        <div className="segmented pd__appearance" role="radiogroup" aria-label="Appearance">
          {[false, true].map((shiny) => (
            <button
              key={String(shiny)}
              type="button"
              role="radio"
              aria-checked={displayedShiny === shiny}
              className={displayedShiny === shiny ? "is-active" : ""}
              onClick={() => {
                setSelectedShiny(shiny);
                setSelectedId(null);
              }}
            >
              {shiny ? "✨ Shiny" : "Normal"}
            </button>
          ))}
        </div>
      )}

      <div className="pd__identity">
        <span className="pd__art">
          <Sprite
            speciesId={species.species_id}
            shiny={displayedShiny}
            animated
            size={104}
            form={spriteForm}
            alt={name}
          />
        </span>
        <div className="pd__who">
          <h2 className="pd__name">{name}</h2>
          <span className={`pd__rarity pd__rarity--${rarity}`}>{titleCase(rarity)}</span>
          {displayedShiny && <span className="pd__shiny">✨ Shiny</span>}
          {individual?.is_active && <span className="pd__raising">Raising</span>}
        </div>
      </div>

      {isUnown && (
        <div className="pd__card">
          <h3 className="pd__title">Unown forms {unownForms.length}/28</h3>
          <div className="pd__unown">
            {UNOWN_FORMS.map(({ form, symbol: sym }) => {
              const owned = unownForms.find((f) => f.form === form);
              return (
                <button
                  key={form}
                  type="button"
                  className={`pd__form ${selectedForm === form ? "is-selected" : ""}`}
                  disabled={!owned}
                  title={owned ? `Unown [${sym}]` : "Not collected"}
                  aria-label={owned ? `Unown ${sym}` : `Unown ${sym}, not collected`}
                  aria-pressed={selectedForm === form}
                  onClick={() => chooseForm(form)}
                >
                  <Sprite
                    speciesId={UNOWN_ID}
                    shiny={owned ? !owned.has_normal : false}
                    animated={false}
                    size={32}
                    form={form}
                    alt=""
                  />
                  <span className="pd__formSym">{sym}</span>
                  {owned?.is_shiny && (
                    <span className="pd__formShiny" aria-hidden="true">
                      ✨
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {!current ? (
        <p className="pd__status">Loading Pokémon details…</p>
      ) : current.status === "error" ? (
        <div className="pd__status">
          <p>Pokémon details could not be loaded.</p>
          <button type="button" className="btn btn--ghost" onClick={() => setAttempt((n) => n + 1)}>
            Retry
          </button>
        </div>
      ) : (
        <DetailBody
          data={current.data}
          individuals={individuals}
          individual={individual}
          language={language}
          onSelect={setSelectedId}
        />
      )}
    </section>
  );
}

function DetailBody({
  data,
  individuals,
  individual,
  language,
  onSelect,
}: {
  data: PokemonDetailView;
  individuals: IndividualView[];
  individual: IndividualView | null;
  language: string;
  onSelect: (id: string) => void;
}) {
  return (
    <>
      {individuals.length > 0 ? (
        <label className="pd__picker">
          <span>Individual</span>
          <select value={individual?.id ?? ""} onChange={(e) => onSelect(e.target.value)}>
            {individuals.map((ind, i) => (
              <option key={ind.id} value={ind.id}>
                {individualLabel(ind, i + 1, language)}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <p className="pd__reference">Collected appearance · species reference</p>
      )}

      {individual ? (
        <div className="pd__card">
          <h3 className="pd__title">Individual</h3>
          <div className="pd__pairs">
            <Pair label="Level" value={String(individual.level)} />
            <Pair
              label="Gender"
              value={individual.gender ? GENDER_LABELS[individual.gender] : "—"}
            />
            <Pair
              label="Nature"
              value={
                individual.nature_label ?? (individual.nature ? slugLabel(individual.nature) : "—")
              }
            />
          </div>
          <Pair
            label="Ability"
            value={
              individual.ability_name
                ? `${slugLabel(individual.ability_name)}${individual.ability_is_hidden ? " · Hidden Ability" : ""}`
                : "—"
            }
          />
          <h3 className="pd__title">Actual stats</h3>
          <StatRows stats={individual.stats} scale={individual.stat_scale} withIv />
          <h3 className="pd__title">Known moves</h3>
          {individual.moves.length === 0 ? (
            <p className="pd__text">No level-up moves learned at this level.</p>
          ) : (
            <ul className="pd__moves">
              {individual.moves.map((m) => (
                <li key={m.name}>
                  <span>{slugLabel(m.name)}</span>
                  <span className="pd__dim">Lv. {m.learned_at_level}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="pd__card">
          <h3 className="pd__title">Base stats</h3>
          <StatRows stats={data.base_stats} scale={300} />
        </div>
      )}

      <div className="pd__card">
        <h3 className="pd__title">Species data</h3>
        <div className="pd__types">
          {data.types.map((t) => (
            <span key={t} className="pd__type">
              {slugLabel(t)}
            </span>
          ))}
        </div>
        <div className="pd__pairs">
          <Pair label="Height" value={`${data.height_m.toFixed(1)} m`} />
          <Pair label="Weight" value={`${data.weight_kg.toFixed(1)} kg`} />
          <Pair label="Base total" value={String(data.base_total)} />
        </div>
        <h3 className="pd__title">Possible abilities</h3>
        <p className="pd__text pd__dim">
          {data.abilities.length === 0
            ? "—"
            : data.abilities
                .map((a) => `${slugLabel(a.name)}${a.is_hidden ? " (Hidden)" : ""}`)
                .join(" · ")}
        </p>
      </div>

      <div className="pd__card">
        <h3 className="pd__title">Complete move list · {data.moves.length}</h3>
        <ul className="pd__moves pd__moves--all">
          {data.moves.map((m) => (
            <li key={m.name}>
              <span>{slugLabel(m.name)}</span>
              <span className="pd__dim">{moveMethodsText(m.methods)}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div className="pd__pair">
      <span className="pd__pairLabel">{label}</span>
      <span className="pd__pairValue">{value}</span>
    </div>
  );
}

function StatRows({
  stats,
  scale,
  withIv = false,
}: {
  stats: StatView[];
  scale: number;
  withIv?: boolean;
}) {
  return (
    <div className="pd__stats">
      {stats.map((s) => {
        const value = withIv ? (s.value ?? s.base) : s.base;
        const label = STAT_LABELS[s.stat] ?? slugLabel(s.stat);
        return (
          <div key={s.stat} className={`pd__stat ${withIv ? "pd__stat--iv" : ""}`}>
            <span className="pd__statLabel">{label}</span>
            <Meter value={scale > 0 ? value / scale : 0} label={`${label} ${value}`} />
            <span className="pd__statValue">{value}</span>
            {withIv && <span className="pd__dim pd__statIv">IV {s.iv ?? "—"}</span>}
          </div>
        );
      })}
    </div>
  );
}
