import { Link } from "react-router-dom"
import { RefreshCw } from "lucide-react"
import { useWatchlist } from "@/hooks/useStocks"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

const sources: Record<string, string> = { sina: "新浪财经", eastmoney: "东方财富", xueqiu: "雪球", adata: "adata", qmt: "QMT" }

export default function WatchlistQuotes() {
  const { data, isLoading, isFetching, error, refetch } = useWatchlist()
  return (
    <Card className="gap-0 py-0">
      <CardHeader className="flex flex-row items-center justify-between px-4 py-3">
        <CardTitle className="text-title">自选行情</CardTitle>
        <Button variant="ghost" size="sm" disabled={isFetching} onClick={() => void refetch()}>
          <RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} />
          {isFetching ? "取数中" : "刷新行情"}
        </Button>
      </CardHeader>
      <CardContent className="px-4 pb-3">
        {error && <p role="alert" className="mb-3 text-sm text-destructive">行情加载失败：{error.message}。可点击刷新重试。</p>}
        {isLoading ? <Skeleton className="h-36 w-full" /> : data?.length ? (
          <table className="w-full text-sm">
            <thead className="border-b text-xs text-muted-foreground">
              <tr><th className="py-2 text-left font-normal">股票</th><th className="text-right font-normal">价格（元）</th><th className="text-right font-normal">涨跌幅</th><th className="text-right font-normal">行情时间（北京时间）</th><th className="text-right font-normal">来源</th></tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {data.map((stock) => (
                <tr key={stock.symbol} className="hover:bg-muted/40">
                  <td className="py-3"><Link className="font-medium hover:underline" to={`/stock/${stock.symbol}`}>{stock.name}<span className="ml-2 font-numeric text-xs text-muted-foreground">{stock.symbol}</span></Link></td>
                  <td className="text-right font-numeric">{stock.close?.toFixed(2) ?? "未取得"}</td>
                  <td className={`text-right font-numeric ${stock.pct_change == null ? "text-muted-foreground" : stock.pct_change > 0 ? "text-up" : stock.pct_change < 0 ? "text-down" : ""}`}>{stock.pct_change == null ? "—" : `${stock.pct_change > 0 ? "+" : ""}${stock.pct_change.toFixed(2)}%`}</td>
                  <td className="text-right font-numeric text-xs text-muted-foreground">{stock.date ? new Date(stock.date).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "来源未提供时间"}</td>
                  <td className="text-right text-xs text-muted-foreground">{stock.source ? sources[stock.source] ?? stock.source : stock.date ? "本地历史数据" : "未知"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : !error && <p className="py-4 text-sm text-muted-foreground">尚未添加自选股。<Link className="ml-1 underline" to="/settings">管理自选股</Link></p>}
      </CardContent>
    </Card>
  )
}
