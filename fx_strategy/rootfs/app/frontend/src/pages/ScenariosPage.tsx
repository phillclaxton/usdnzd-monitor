import { useState } from 'react';

import { Banner, Card, EmptyState, Field, Loading, Tag } from '@/components/ui';
import { useScenarios, useStrategies } from '@/hooks/useStrategy';
import { useSettings } from '@/hooks/useSettings';
import { formatDecimal, formatRate } from '@/lib/decimal';

export default function ScenariosPage() {
  const strategies = useStrategies();
  const settings = useSettings();
  const [customRate, setCustomRate] = useState('');
  const [applied, setApplied] = useState('');
  const strategyId = strategies.data?.[0]?.id ?? null;
  const scenarios = useScenarios(strategyId, applied || undefined);

  const ratePlaces = settings.data?.formatting.rate_decimal_places ?? 4;

  if (strategies.isLoading) return <Loading label="Loading…" />;
  if (strategyId === null) {
    return (
      <Card title="Scenario comparison">
        <EmptyState glyph="⚖" title="No strategy to compare">
          <p>Create a strategy first, then come back to compare it with the alternatives.</p>
        </EmptyState>
      </Card>
    );
  }

  const rows = scenarios.data?.scenarios ?? [];

  return (
    <>
      <Card
        title="Scenario comparison"
        subtitle="Up to four plans for the amount still unconverted, side by side."
      >
        <form
          className="fx-inline"
          onSubmit={(event) => {
            event.preventDefault();
            setApplied(customRate);
          }}
        >
          <Field label="Add your own rate" hint="a level you think is achievable" htmlFor="custom">
            <input
              id="custom"
              type="text"
              inputMode="decimal"
              placeholder="1.8500"
              value={customRate}
              onChange={(event) => setCustomRate(event.target.value)}
            />
          </Field>
          <button type="submit">Compare</button>
        </form>

        {scenarios.isLoading && <Loading />}
        {scenarios.isError && (
          <Banner tone="error">{(scenarios.error as Error).message}</Banner>
        )}

        {rows.length === 0 && !scenarios.isLoading && (
          <EmptyState glyph="⚖" title="Nothing to compare yet">
            <p>
              Scenarios need a current rate and an unconverted balance. Refresh a rate on the
              dashboard first.
            </p>
          </EmptyState>
        )}

        {rows.length > 0 && (
          <div className="fx-table-wrap">
            <table className="fx-table">
              <thead>
                <tr>
                  <th className="fx-left">Measure</th>
                  {rows.map((scenario) => (
                    <th key={scenario.key}>{scenario.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="fx-left">Blended rate</td>
                  {rows.map((scenario) => (
                    <td key={scenario.key}>{formatRate(scenario.blended_rate, ratePlaces)}</td>
                  ))}
                </tr>
                <tr>
                  <td className="fx-left">
                    Gross proceeds <Tag quality="gross" />
                  </td>
                  {rows.map((scenario) => (
                    <td key={scenario.key}>{formatDecimal(scenario.gross_target_amount)}</td>
                  ))}
                </tr>
                <tr>
                  <td className="fx-left">
                    Estimated fees <Tag quality="estimate" />
                  </td>
                  {rows.map((scenario) => (
                    <td key={scenario.key}>
                      {scenario.fee.available
                        ? formatDecimal(scenario.fee.amount_target_currency)
                        : 'Not included'}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="fx-left">
                    Estimated net <Tag quality="estimate" />
                  </td>
                  {rows.map((scenario) => (
                    <td key={scenario.key}>
                      {scenario.net_target_amount === null
                        ? '—'
                        : formatDecimal(scenario.net_target_amount)}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="fx-left">Amount exposed to future moves</td>
                  {rows.map((scenario) => (
                    <td key={scenario.key}>{formatDecimal(scenario.exposed_source_amount)}</td>
                  ))}
                </tr>
                <tr>
                  <td className="fx-left">Cost of a 1-cent move while waiting</td>
                  {rows.map((scenario) => (
                    <td key={scenario.key}>{formatDecimal(scenario.one_cent_exposure)}</td>
                  ))}
                </tr>
                <tr>
                  <td className="fx-left">Rate the plan depends on</td>
                  {rows.map((scenario) => (
                    <td key={scenario.key}>
                      {scenario.rate_required === null
                        ? 'None'
                        : formatRate(scenario.rate_required, ratePlaces)}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td className="fx-left">Conversions</td>
                  {rows.map((scenario) => (
                    <td key={scenario.key}>{scenario.legs.length}</td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        )}

        {scenarios.data && <Banner tone="info">{scenarios.data.note}</Banner>}

        {rows.map((scenario) => (
          <div key={scenario.key} style={{ marginTop: 'var(--fx-gap)' }}>
            <h3 style={{ fontSize: '0.9rem', margin: '0 0 4px' }}>{scenario.name}</h3>
            <p className="fx-stat-note" style={{ marginTop: 0 }}>
              {scenario.description}
            </p>
            <ul className="fx-stat-note" style={{ margin: 0, paddingLeft: 18 }}>
              {scenario.assumptions.map((assumption) => (
                <li key={assumption}>{assumption}</li>
              ))}
            </ul>
          </div>
        ))}
      </Card>
    </>
  );
}
