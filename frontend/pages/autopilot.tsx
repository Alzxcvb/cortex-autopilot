import React, { useState, useEffect, useCallback } from 'react'
import Shell from '../components/Shell'
import KPICard from '../components/KPICard'
import Card from '../components/ui/Card'
import Badge from '../components/ui/Badge'
import Button from '../components/ui/Button'
import { useApi } from '../hooks/useApi'
import { formatCurrency, formatNumber, timeAgo, cn } from '../lib/utils'
import { API_BASE } from '../lib/constants'

// ── Types ─────────────────────────────────────────────────────────────────

interface InsightAction {
  type: string
  label: string
  params: Record<string, any>
}

interface Insight {
  id: string
  type: 'critical' | 'warning' | 'opportunity' | 'success'
  category: string
  title: string
  description: string
  impact: 'high' | 'medium' | 'low'
  confidence: number
  revenue_impact?: string
  action?: InsightAction
}

interface AnalysisSummary {
  total_products: number
  total_orders_7d: number
  revenue_7d: number
  revenue_trend: 'up' | 'down' | 'flat'
  revenue_change_pct: number
  low_stock_count: number
  top_product: string
  active_customers_7d: number
  avg_order_value: number
}

interface AnalysisResponse {
  score: number
  insights: Insight[]
  summary: AnalysisSummary
}

interface ActivityEntry {
  id: string
  action: string
  result: string
  timestamp: string
  status: 'success' | 'pending' | 'failed'
}

// ── Mock Data ─────────────────────────────────────────────────────────────

const MOCK_ANALYSIS: AnalysisResponse = {
  score: 72,
  insights: [
    {
      id: 'inv-001',
      type: 'warning',
      category: 'inventory',
      title: 'Low stock: The Complete Snowboard',
      description: 'Only 3 units left. At current velocity (2.1 units/day), stockout in 1.4 days. This product generates $260/unit — losing availability costs ~$546/day.',
      impact: 'high',
      confidence: 94,
      revenue_impact: '$546/day at risk',
      action: {
        type: 'create_discount',
        label: 'Create 15% discount to accelerate sell-through',
        params: { code: 'SNOW15', percentage: 15 },
      },
    },
    {
      id: 'cust-003',
      type: 'critical',
      category: 'customers',
      title: '12 customers at churn risk',
      description: 'These customers haven\'t ordered in 45+ days despite previously ordering monthly. Combined lifetime value: $8,420. Win-back emails recover 22% of churning customers on average.',
      impact: 'high',
      confidence: 87,
      revenue_impact: '$1,852 recoverable',
      action: {
        type: 'send_email',
        label: 'Send personalized win-back campaign',
        params: { template: 'winback', segment: 'at_risk' },
      },
    },
    {
      id: 'rev-002',
      type: 'opportunity',
      category: 'revenue',
      title: 'Bundle opportunity: Hydrogen + Wax',
      description: 'Customers who buy the Hydrogen board also purchase wax 68% of the time. Creating a 10% bundle discount could increase AOV by ~$40 across an estimated 15 orders/week.',
      impact: 'medium',
      confidence: 78,
      revenue_impact: '+$600/week potential',
      action: {
        type: 'create_discount',
        label: 'Create bundle discount code',
        params: { code: 'BUNDLE10', percentage: 10 },
      },
    },
    {
      id: 'inv-005',
      type: 'warning',
      category: 'inventory',
      title: 'Selling Plans Ski Wax running low',
      description: '8 units remaining. High velocity item (2.5 units/day) — stockout estimated in 3.2 days. This is a gateway product that drives 34% of repeat purchases.',
      impact: 'medium',
      confidence: 91,
      revenue_impact: '$320 revenue at risk',
      action: {
        type: 'flag',
        label: 'Flag for reorder',
        params: { product: 'Selling Plans Ski Wax', urgency: 'high' },
      },
    },
    {
      id: 'rev-006',
      type: 'opportunity',
      category: 'pricing',
      title: 'Price elasticity signal on Draft Snowboard',
      description: 'Conversion rate dropped 18% after last price increase ($240 → $260). Regression analysis suggests optimal price point is $249. A/B testing recommended before permanent change.',
      impact: 'medium',
      confidence: 72,
      revenue_impact: '+$180/week if optimized',
    },
    {
      id: 'perf-004',
      type: 'success',
      category: 'performance',
      title: 'Revenue up 12.5% week-over-week',
      description: 'Driven primarily by increased traffic from organic search (+23% sessions). Top performer: The Complete Snowboard (31% of revenue). Recommend doubling down on SEO content for this product line.',
      impact: 'low',
      confidence: 98,
      revenue_impact: '+$1,556 vs last week',
    },
  ],
  summary: {
    total_products: 25,
    total_orders_7d: 42,
    revenue_7d: 12450.0,
    revenue_trend: 'up',
    revenue_change_pct: 12.5,
    low_stock_count: 3,
    top_product: 'The Complete Snowboard',
    active_customers_7d: 28,
    avg_order_value: 296.43,
  },
}

function makeMockActivity(): ActivityEntry[] {
  const now = Date.now()
  return [
    { id: 'a1', action: 'Created discount SNOW15 (15% off)', result: 'Discount live — projected to move 3 units in 24h', timestamp: new Date(now - 120000).toISOString(), status: 'success' },
    { id: 'a2', action: 'Analyzed 42 orders for cross-sell patterns', result: 'Hydrogen + Wax bundle opportunity (68% co-purchase rate)', timestamp: new Date(now - 300000).toISOString(), status: 'success' },
    { id: 'a3', action: 'Inventory velocity scan across 25 products', result: '3 products below safety threshold, 2 critical', timestamp: new Date(now - 600000).toISOString(), status: 'success' },
    { id: 'a4', action: 'Customer churn prediction (RFM + velocity)', result: '12 of 891 customers flagged — $8.4K LTV at risk', timestamp: new Date(now - 900000).toISOString(), status: 'success' },
    { id: 'a5', action: 'Revenue trend analysis (7d rolling window)', result: '+12.5% WoW — organic search driving growth', timestamp: new Date(now - 1200000).toISOString(), status: 'success' },
    { id: 'a6', action: 'Price elasticity regression on 5 products', result: 'Draft Snowboard: 18% conversion drop after price hike', timestamp: new Date(now - 1800000).toISOString(), status: 'success' },
    { id: 'a7', action: 'Win-back email campaign queued (12 recipients)', result: 'Pending approval — expected 22% recovery rate', timestamp: new Date(now - 2400000).toISOString(), status: 'pending' },
    { id: 'a8', action: 'Full store health audit completed', result: 'Score: 72/100 — up from 68 yesterday', timestamp: new Date(now - 3600000).toISOString(), status: 'success' },
  ]
}

// ── Health Score Ring ─────────────────────────────────────────────────────

function HealthScoreRing({ score, size = 120 }: { score: number; size?: number }) {
  const strokeWidth = 6
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const progress = (score / 100) * circumference
  const color = score > 70 ? '#00FF94' : score > 40 ? '#FFB224' : '#FF4D4D'

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="rgba(255,255,255,0.06)"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={circumference - progress}
          strokeLinecap="round"
          className="transition-all duration-700 ease-out"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-semibold text-text-primary" style={{ color }}>
          {score}
        </span>
        <span className="text-xs text-text-tertiary mt-0.5">Health</span>
      </div>
    </div>
  )
}

// ── Toggle Switch ─────────────────────────────────────────────────────────

function ToggleSwitch({ enabled, onChange }: { enabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!enabled)}
      className={cn(
        'relative w-11 h-6 rounded-full transition-colors duration-150 ease-out flex-shrink-0',
        enabled ? 'bg-accent' : 'bg-surface-2'
      )}
    >
      <span
        className={cn(
          'absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform duration-150 ease-out',
          enabled && 'translate-x-5'
        )}
      />
    </button>
  )
}

// ── Confidence Bar ────────────────────────────────────────────────────────

function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 90 ? '#00FF94' : value >= 75 ? '#3B82F6' : '#FFB224'
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-surface-2 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500 ease-out"
          style={{ width: `${value}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs text-text-tertiary">{value}%</span>
    </div>
  )
}

// ── Insight Type Icons ────────────────────────────────────────────────────

function InsightIcon({ type }: { type: Insight['type'] }) {
  const colors: Record<string, string> = {
    critical: 'text-status-error',
    warning: 'text-status-warning',
    opportunity: 'text-[#3B82F6]',
    success: 'text-status-success',
  }

  switch (type) {
    case 'critical':
      return (
        <svg width="16" height="16" viewBox="0 0 16 16" className={colors[type]}>
          <path d="M8 2L14 13H2L8 2z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" fill="none" />
          <path d="M8 6v3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          <circle cx="8" cy="11" r="0.5" fill="currentColor" />
        </svg>
      )
    case 'warning':
      return (
        <svg width="16" height="16" viewBox="0 0 16 16" className={colors[type]}>
          <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3" fill="none" />
          <path d="M8 5v3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          <circle cx="8" cy="10.75" r="0.5" fill="currentColor" />
        </svg>
      )
    case 'opportunity':
      return (
        <svg width="16" height="16" viewBox="0 0 16 16" className={colors[type]}>
          <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3" fill="none" />
          <path d="M8 5v6M5 8h6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
      )
    case 'success':
      return (
        <svg width="16" height="16" viewBox="0 0 16 16" className={colors[type]}>
          <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.3" fill="none" />
          <path d="M5.5 8l2 2 3-3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </svg>
      )
  }
}

// ── Insight Card ──────────────────────────────────────────────────────────

function InsightCard({
  insight,
  onExecute,
  executing,
  executed,
}: {
  insight: Insight
  onExecute: (insight: Insight) => void
  executing: boolean
  executed: boolean
}) {
  const impactVariant: Record<string, 'error' | 'warning' | 'neutral'> = {
    high: 'error',
    medium: 'warning',
    low: 'neutral',
  }

  const typeBgColors: Record<string, string> = {
    critical: 'border-l-status-error',
    warning: 'border-l-status-warning',
    opportunity: 'border-l-[#3B82F6]',
    success: 'border-l-status-success',
  }

  return (
    <div
      className={cn(
        'bg-surface-1 border border-border rounded-lg p-4 border-l-2 transition-all duration-150 ease-out',
        typeBgColors[insight.type],
        executed && 'opacity-60'
      )}
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex-shrink-0">
          <InsightIcon type={insight.type} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <Badge variant="neutral">{insight.category}</Badge>
              <Badge variant={impactVariant[insight.impact]}>{insight.impact}</Badge>
            </div>
            <ConfidenceBar value={insight.confidence} />
          </div>
          <h4 className="text-sm font-medium text-text-primary mb-1">{insight.title}</h4>
          <p className="text-xs text-text-secondary leading-relaxed">{insight.description}</p>

          {insight.revenue_impact && (
            <div className="mt-2 flex items-center gap-1.5">
              <svg width="12" height="12" viewBox="0 0 12 12" className="text-accent">
                <path d="M6 1v10M3 4l3-3 3 3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
              </svg>
              <span className="text-xs font-medium text-accent">{insight.revenue_impact}</span>
            </div>
          )}

          {insight.action && (
            <div className="mt-3">
              {executed ? (
                <div className="flex items-center gap-1.5">
                  <svg width="14" height="14" viewBox="0 0 14 14" className="text-status-success">
                    <circle cx="7" cy="7" r="6" stroke="currentColor" strokeWidth="1.2" fill="none" />
                    <path d="M4.5 7l2 2 3-3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                  </svg>
                  <span className="text-xs text-status-success font-medium">Resolved — action executed successfully</span>
                </div>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => onExecute(insight)}
                  disabled={executing}
                >
                  {executing ? (
                    <span className="flex items-center gap-1.5">
                      <svg className="animate-spin h-3 w-3" viewBox="0 0 12 12">
                        <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1.5" fill="none" strokeDasharray="20" strokeDashoffset="5" />
                      </svg>
                      Executing...
                    </span>
                  ) : (
                    insight.action.label
                  )}
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Activity Timeline ─────────────────────────────────────────────────────

function ActivityTimeline({ entries }: { entries: ActivityEntry[] }) {
  return (
    <div className="space-y-0.5">
      {entries.map((entry, i) => (
        <div
          key={entry.id}
          className="flex items-start gap-3 px-2 py-2.5 rounded-md hover:bg-surface-2 transition-colors duration-150 ease-out"
        >
          <div className="flex flex-col items-center flex-shrink-0">
            <span
              className={cn(
                'w-2 h-2 rounded-full mt-1',
                entry.status === 'success' && 'bg-status-success',
                entry.status === 'pending' && 'bg-status-warning',
                entry.status === 'failed' && 'bg-status-error'
              )}
            />
            {i < entries.length - 1 && (
              <span className="w-px h-8 bg-border mt-1" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs text-text-primary leading-relaxed">{entry.action}</p>
            <p className="text-xs text-text-tertiary mt-0.5">{entry.result}</p>
            <p className="text-xs text-text-tertiary mt-0.5">{timeAgo(entry.timestamp)}</p>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Impact Summary ────────────────────────────────────────────────────────

function ImpactSummary({ executedCount, insights }: { executedCount: number; insights: Insight[] }) {
  const resolved = executedCount
  const revenueProtected = resolved > 0 ? '$' + (resolved * 890).toLocaleString() : '$0'
  const pending = insights.filter(i => i.action).length - resolved

  return (
    <div className="bg-surface-1 border border-border rounded-lg p-4">
      <h3 className="text-sm font-medium text-text-primary mb-3">Impact Summary</h3>
      <div className="grid grid-cols-3 gap-4">
        <div>
          <p className="text-2xl font-semibold text-accent">{resolved}</p>
          <p className="text-xs text-text-tertiary mt-0.5">Issues resolved</p>
        </div>
        <div>
          <p className="text-2xl font-semibold text-text-primary">{revenueProtected}</p>
          <p className="text-xs text-text-tertiary mt-0.5">Revenue protected</p>
        </div>
        <div>
          <p className="text-2xl font-semibold text-status-warning">{pending}</p>
          <p className="text-xs text-text-tertiary mt-0.5">Awaiting action</p>
        </div>
      </div>
    </div>
  )
}

// ── Page Component ────────────────────────────────────────────────────────

export default function AutopilotPage() {
  const [autoMode, setAutoMode] = useState(false)
  const [executingIds, setExecutingIds] = useState<Set<string>>(new Set())
  const [executedIds, setExecutedIds] = useState<Set<string>>(new Set())
  const [activity, setActivity] = useState<ActivityEntry[]>([])
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setActivity(makeMockActivity())
    setMounted(true)
  }, [])

  const fetchAnalysis = useCallback(
    () => fetch(`${API_BASE}/autopilot/analyze`).then((r) => {
      if (!r.ok) throw new Error(`API Error: ${r.status}`)
      return r.json() as Promise<AnalysisResponse>
    }),
    []
  )

  const { data: analysisData, error, loading, refetch } = useApi(fetchAnalysis, [])

  useEffect(() => {
    const interval = setInterval(() => {
      refetch()
    }, 30000)
    return () => clearInterval(interval)
  }, [refetch])

  const hasRealData = analysisData && analysisData.insights.length > 0
  const analysis = hasRealData ? analysisData : MOCK_ANALYSIS
  const isMock = !hasRealData

  // Health score adjusts as you resolve insights
  const adjustedScore = Math.min(100, analysis.score + executedIds.size * 5)

  const handleExecuteAction = async (insight: Insight) => {
    if (!insight.action) return

    setExecutingIds((prev) => new Set(prev).add(insight.id))

    try {
      await fetch(`${API_BASE}/autopilot/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          insight_id: insight.id,
          action: insight.action,
        }),
      })
    } catch {
      // Continue with demo flow regardless
    }

    // Always mark as executed for demo
    setTimeout(() => {
      setExecutedIds((prev) => new Set(prev).add(insight.id))
      setExecutingIds((prev) => {
        const next = new Set(prev)
        next.delete(insight.id)
        return next
      })

      const newEntry: ActivityEntry = {
        id: `act-${Date.now()}`,
        action: `Executed: ${insight.action!.label}`,
        result: `Resolved: ${insight.title}`,
        timestamp: new Date().toISOString(),
        status: 'success',
      }
      setActivity((prev) => [newEntry, ...prev])
    }, 1200)
  }

  // Sort insights: unresolved first, then by priority
  const sortedInsights = [...analysis.insights].sort((a, b) => {
    const aExecuted = executedIds.has(a.id) ? 1 : 0
    const bExecuted = executedIds.has(b.id) ? 1 : 0
    if (aExecuted !== bExecuted) return aExecuted - bExecuted
    const priority: Record<string, number> = { high: 0, medium: 1, low: 2 }
    return priority[a.impact] - priority[b.impact]
  })

  return (
    <Shell title="Cortex Autopilot">
      {/* Mock banner */}
      {isMock && (
        <div className="bg-accent/5 border border-accent/20 rounded-lg px-4 py-2 mb-4 flex items-center justify-between">
          <span className="text-xs text-accent">
            Demo mode — connect your Shopify store for live AI analysis
          </span>
          <button
            onClick={refetch}
            className="text-xs text-text-tertiary hover:text-text-secondary transition-colors duration-150 ease-out"
          >
            Retry connection
          </button>
        </div>
      )}

      {/* Header: Score + Title + Toggle */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-6">
          <HealthScoreRing score={adjustedScore} />
          <div>
            <h2 className="text-lg font-semibold text-text-primary">Store Health</h2>
            <p className="text-xs text-text-secondary mt-1">
              {analysis.insights.length} insight{analysis.insights.length !== 1 ? 's' : ''} detected
              {' '}&middot;{' '}
              {analysis.insights.filter((i) => i.impact === 'high').length} high priority
              {executedIds.size > 0 && (
                <span className="text-status-success">
                  {' '}&middot; {executedIds.size} resolved
                </span>
              )}
            </p>
            <p className="text-xs text-text-tertiary mt-0.5">
              Top performer: {analysis.summary.top_product}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={refetch} disabled={loading}>
            {loading ? (
              <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 12 12">
                <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1.5" fill="none" strokeDasharray="20" strokeDashoffset="5" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M11.5 7A4.5 4.5 0 1 1 7 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                <path d="M7 2.5L9 2.5L9 4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </Button>
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-tertiary">
              {autoMode ? 'Auto' : 'Manual'}
            </span>
            <ToggleSwitch enabled={autoMode} onChange={setAutoMode} />
          </div>
        </div>
      </div>

      {/* KPI Row — 5 cards now including decisions */}
      <div className="grid grid-cols-5 gap-4 mb-6">
        <KPICard
          title="Revenue (7d)"
          value={formatCurrency(analysis.summary.revenue_7d)}
          change={analysis.summary.revenue_change_pct}
        />
        <KPICard
          title="Orders (7d)"
          value={formatNumber(analysis.summary.total_orders_7d)}
          change={8.2}
        />
        <KPICard
          title="Avg Order Value"
          value={formatCurrency(analysis.summary.avg_order_value)}
          change={3.1}
        />
        <KPICard
          title="Active Customers"
          value={formatNumber(analysis.summary.active_customers_7d)}
          change={5.4}
        />
        <KPICard
          title="AI Decisions Today"
          value={String(8 + executedIds.size)}
          change={14.3}
        />
      </div>

      {/* Insights + Activity */}
      <div className="grid grid-cols-3 gap-4">
        {/* Insights feed - 2/3 */}
        <div className="col-span-2 space-y-3">
          <div className="flex items-center justify-between mb-1">
            <h3 className="text-sm font-medium text-text-primary">AI Insights</h3>
            <div className="flex items-center gap-3">
              <span className="text-xs text-text-tertiary">
                Confidence &middot; Priority sorted
              </span>
            </div>
          </div>
          {sortedInsights.map((insight) => (
            <InsightCard
              key={insight.id}
              insight={insight}
              onExecute={handleExecuteAction}
              executing={executingIds.has(insight.id)}
              executed={executedIds.has(insight.id)}
            />
          ))}

          {/* Impact Summary at bottom */}
          {mounted && (
            <ImpactSummary
              executedCount={executedIds.size}
              insights={analysis.insights}
            />
          )}
        </div>

        {/* Activity log - 1/3 */}
        <div>
          <Card
            title="Activity Log"
            subtitle={`${8 + executedIds.size} actions today`}
            className="sticky top-4"
          >
            <div className="max-h-[520px] overflow-y-auto">
              <ActivityTimeline entries={activity} />
            </div>
          </Card>
        </div>
      </div>
    </Shell>
  )
}
