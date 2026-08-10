import { useState, useEffect } from 'react'
import ReactECharts from 'echarts-for-react'
import client from '../api/client'

const CATEGORIES = ['银行金融', '科技互联网', '新能源', '大消费', '高端制造', '周期资源']
const PERIODS = ['1月', '3月', '1年', '5年', '全部']

interface IndexData {
  code: string
  name: string
  price: number
  change_pct: number
}

interface StockItem {
  code: string
  name: string
  price: number
  change_pct: number
}

interface SectorData {
  category: string
  period: string
  sectors: { name: string; price: number; change_pct: number }[]
  stocks: StockItem[]
  updated_at: string
}

export default function Home() {
  const [category, setCategory] = useState(CATEGORIES[0])
  const [period, setPeriod] = useState(PERIODS[0])
  const [indices, setIndices] = useState<IndexData[]>([])
  const [sectorData, setSectorData] = useState<SectorData | null>(null)
  const [updatedTime, setUpdatedTime] = useState('')
  const [loading, setLoading] = useState(false)

  const fetchIndices = async () => {
    try {
      const resp = await client.get('/stocks/market-indices')
      setIndices(resp.data.data || [])
    } catch {/* ignore */}
  }

  const fetchSector = async () => {
    setLoading(true)
    try {
      const resp = await client.get('/stocks/sectors/overview', { params: { category, period } })
      const data: SectorData = resp.data.data
      setSectorData(data)
      setUpdatedTime(new Date().toLocaleTimeString('zh-CN'))
    } catch {/* ignore */}
    finally { setLoading(false) }
  }

  useEffect(() => {
    fetchIndices()
    fetchSector()
    const id = setInterval(fetchIndices, 60000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchSector()
  }, [category, period])

  const klineOption = {
    title: { text: sectorData ? `${sectorData.category}板块走势` : '板块走势', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: sectorData?.sectors.map((s) => s.name) || [] },
    yAxis: { type: 'value', name: '涨跌幅 %' },
    series: [{
      type: 'bar',
      data: sectorData?.sectors.map((s) => s.change_pct) || [],
      itemStyle: {
        color: (p: any) => (p.value >= 0 ? '#ef4444' : '#22c55e'),
      },
    }],
    grid: { left: '10%', right: '10%', bottom: '10%', top: '15%' },
  }

  const heatData = indices.map((idx, i) => ({
    name: idx.name,
    value: [i, 0, idx.change_pct],
  }))
  const heatmapOption = {
    title: { text: '大盘指数热力图', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: {
      formatter: (p: any) => {
        const d = indices[p.dataIndex]
        return d ? `${d.name}: ${d.price} (${d.change_pct >= 0 ? '+' : ''}${d.change_pct}%)` : ''
      },
    },
    grid: { left: '5%', right: '5%', top: '15%', bottom: '15%' },
    xAxis: { type: 'category', data: indices.map((d) => d.name), axisLabel: { fontSize: 11 }, splitArea: { show: false } },
    yAxis: { type: 'category', data: ['涨跌幅'], axisLabel: { show: false }, splitArea: { show: false } },
    visualMap: {
      min: -3,
      max: 3,
      show: false,
      inRange: { color: ['#22c55e', '#f0f0f0', '#ef4444'] },
    },
    series: [{
      name: '指数涨跌',
      type: 'heatmap',
      data: heatData,
      label: {
        show: true,
        formatter: (p: any) => {
          const d = indices[p.dataIndex]
          return d ? `${d.change_pct >= 0 ? '+' : ''}${d.change_pct}%` : ''
        },
      },
    }],
  }

  const fmtPct = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
  const color = (v: number) => (v >= 0 ? 'text-red-500' : 'text-green-500')

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-5 gap-4">
        {indices.map((idx) => (
          <div key={idx.code} className="bg-white rounded-lg shadow p-4">
            <p className="text-sm text-gray-500">{idx.name}</p>
            <p className={`text-xl font-bold ${color(idx.change_pct)}`}>
              {idx.price.toFixed(2)}
            </p>
            <p className={`text-sm ${color(idx.change_pct)}`}>{fmtPct(idx.change_pct)}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-400">
          数据更新于 {updatedTime || '加载中...'}
        </span>
        <button
          onClick={() => { fetchIndices(); fetchSector() }}
          className="text-sm text-brand-600 hover:underline"
        >
          刷新
        </button>
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex gap-2 mb-4 flex-wrap">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                category === c ? 'bg-brand-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {c}
            </button>
          ))}
        </div>

        <div className="flex gap-2 mb-4 flex-wrap">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 rounded-md text-xs transition-colors ${
                period === p ? 'bg-brand-100 text-brand-700' : 'text-gray-500 hover:bg-gray-100'
              }`}
            >
              {p}
            </button>
          ))}
        </div>

        {sectorData && sectorData.sectors.length > 0 ? (
          <ReactECharts option={klineOption} style={{ height: '300px' }} showLoading={loading} />
        ) : (
          <div className="flex items-center justify-center h-[300px] text-gray-400">
            {loading ? '加载中...' : '暂无板块数据'}
          </div>
        )}
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <h3 className="text-lg font-semibold text-gray-800 mb-4">代表个股</h3>
        <div className="grid grid-cols-5 gap-3">
          {(sectorData?.stocks || []).map((s) => (
            <div key={s.code} className="border border-gray-200 rounded-lg p-3 hover:shadow-md transition-shadow">
              <p className="text-sm font-medium text-gray-700">{s.name}</p>
              <p className="text-xs text-gray-400">{s.code}</p>
              <p className={`text-lg font-bold ${color(s.change_pct)}`}>
                {s.price.toFixed(2)}
              </p>
              <p className={`text-sm ${color(s.change_pct)}`}>{fmtPct(s.change_pct)}</p>
            </div>
          ))}
          {(!sectorData?.stocks || sectorData.stocks.length === 0) && (
            <p className="col-span-5 text-center text-gray-400 py-8">暂无数据</p>
          )}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        {indices.length > 0 ? (
          <ReactECharts option={heatmapOption} style={{ height: '120px' }} />
        ) : (
          <div className="flex items-center justify-center h-[120px] text-gray-400 text-sm">
            指数数据加载中...
          </div>
        )}
      </div>
    </div>
  )
}
