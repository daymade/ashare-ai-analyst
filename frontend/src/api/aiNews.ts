/** API client for AI news endpoints. */

import client from "./client"

export interface AiNewsItem {
  id: number
  title: string
  url: string
  summary: string
  source_id: string
  source_name: string
  category: string
  icon: string
  tags: string[]
  published_at: string | null
  fetched_at: string
  is_read: boolean
}

export interface AiNewsListResponse {
  items: AiNewsItem[]
  total: number
  limit: number
  offset: number
}

export interface AiNewsSource {
  source_id: string
  source_name: string
  category: string
  article_count: number
  latest: string | null
  icon: string
  circuit_open: boolean
  status: "ok" | "error" | "not_fetched"
  last_attempt?: string | null
  error?: string | null
}

export async function fetchAiNews(params?: {
  category?: string
  source?: string
  search?: string
  unread_only?: boolean
  limit?: number
  offset?: number
}): Promise<AiNewsListResponse> {
  const { data } = await client.get<AiNewsListResponse>("/ai-news/", { params, timeout: 15_000 })
  return data
}

export async function fetchAiNewsSources(): Promise<AiNewsSource[]> {
  const { data } = await client.get<AiNewsSource[]>("/ai-news/sources", { timeout: 15_000 })
  return data
}

export async function fetchAiNewsUnreadCount(): Promise<{ count: number }> {
  const { data } = await client.get<{ count: number }>("/ai-news/unread-count")
  return data
}

export async function markAiNewsRead(
  newsId: number
): Promise<{ success: boolean }> {
  const { data } = await client.post<{ success: boolean }>(
    `/ai-news/${newsId}/read`
  )
  return data
}

export async function refreshAiNews(
  source?: string
): Promise<{ new_items: number; by_source: Record<string, number>; failed_sources: string[] }> {
  const { data } = await client.post<{
    new_items: number
    by_source: Record<string, number>
    failed_sources: string[]
  }>("/ai-news/refresh", null, { params: source ? { source } : undefined })
  return data
}
