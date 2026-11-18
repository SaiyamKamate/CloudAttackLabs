import { parseRisk, riskTone } from '../../lib/describe.js'

// The backend's risk score and its explanation, split into the factors the
// backend itself listed ("+40: the highest alert severity is medium.").
export default function RiskBreakdown({ score, explanation }) {
  if (score === null || score === undefined) {
    return <div className="muted">This incident was created before risk scoring existed, so it has no score.</div>
  }
  const { factors, notes } = parseRisk(explanation)
  const tone = riskTone(score)

  return (
    <div className="stack-sm">
      <div className="risk-gauge">
        <div className="risk-score">{score}<small> / 100</small></div>
        <div style={{ flex: 1 }}>
          <div className="risk-track"><div className={`risk-fill ${tone}`} style={{ width: `${score}%` }} /></div>
          <div className="row-between dim small" style={{ marginTop: 4 }}><span>0</span><span>100</span></div>
        </div>
      </div>

      {factors.length > 0 ? (
        <div>
          {factors.map((factor, i) => (
            <div className="factor" key={i}>
              <span className={`factor-pts ${factor.points > 0 ? 'pos' : factor.points < 0 ? 'neg' : 'zero'}`}>
                {factor.points > 0 ? '+' : ''}{factor.points}
              </span>
              <span>{factor.reason}</span>
            </div>
          ))}
        </div>
      ) : explanation && <div className="pre-line">{explanation}</div>}

      {notes.length > 0 && <div className="small dim">{notes.join(' ')}</div>}
    </div>
  )
}
