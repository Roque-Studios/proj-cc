/**
 * Token-based fetch API client for the creator gateway-settings panel.
 *
 * All calls go through `/api/...` — nginx (prod) and the vite dev proxy both
 * strip that prefix and forward to the FastAPI backend. The access token is
 * kept in localStorage and attached as `Authorization: Bearer <token>`.
 * A 401 clears the stored token and throws ApiError so the UI can bounce back
 * to the login page.
 */

const ACCESS_TOKEN_KEY = 'cc_access_token'
const REFRESH_TOKEN_KEY = 'cc_refresh_token'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, access)
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAccessToken()
  const headers: Record<string, string> = {
    ...((options.headers as Record<string, string>) ?? {}),
  }
  const body = options.body
  if (body && !(body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  let resp: Response
  try {
    resp = await fetch(`/api${path}`, { ...options, headers })
  } catch {
    throw new ApiError(0, 'Network error — is the API reachable?')
  }

  if (resp.status === 401) {
    clearTokens()
    throw new ApiError(401, 'Not authenticated')
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    try {
      const data = await resp.json()
      if (data && typeof data.detail === 'string') detail = data.detail
    } catch {
      /* keep the status text fallback */
    }
    throw new ApiError(resp.status, detail)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
}

export interface GatewayField {
  name: string
  label: string
  required: boolean
  secret: boolean
  placeholder: string
  options: string[]
  configured: boolean
}

export interface GatewaySettings {
  gateway: string
  label: string
  description: string
  enabled: boolean
  configured: boolean
  fields: GatewayField[]
}

export interface MessagingSettings {
  allow_messages_from_all_followers: boolean
}

export interface CreatorMedia {
  id: number
  media_type: string
  media_url: string | null
}

export interface CreatorPost {
  id: number
  caption: string | null
  broadcast_price_cents: number | null
  is_visible: boolean
  created_at: string
  updated_at: string
  media_count: number
  view_count: number
  unlock_count: number
  media: CreatorMedia[]
}

export interface PostUpdate {
  caption?: string | null
  is_visible?: boolean
}

export interface Subscriber {
  subscription_id: number
  subscriber_id: number
  subscriber_email: string
  subscriber_username: string | null
  status: string
  current_period_start: string | null
  current_period_end: string | null
  cancel_at_period_end: boolean
  started_at: string
  payment_provider: string | null
}

export interface RevenueSummary {
  monthly_revenue_cents: number
  one_time_revenue_cents: number
  total_revenue_cents: number
  active_subscribers: number
  trialing_subscribers: number
  past_due_subscribers: number
  canceled_subscribers: number
  total_subscribers: number
}

export interface SubscriberList {
  items: Subscriber[]
  page: number
  page_size: number
  total: number
  has_more: boolean
  summary: RevenueSummary
}

export interface SocialLink {
  platform: string
  label: string
  value: string
}

export interface LandingProfile {
  id: number
  username: string | null
  display_name: string | null
  bio: string | null
  avatar_url: string | null
}

export interface LandingViewer {
  level: 'anonymous' | 'registered' | 'follower'
  user_id: number | null
  username: string | null
  subscription: string | null
}

export interface LandingGateway {
  gateway: string
  label: string
}

export interface CreatorLanding {
  profile: LandingProfile
  social_links: SocialLink[]
  viewer: LandingViewer
  gateways: LandingGateway[]
}

export interface FeedPost {
  id: number
  creator_id: number
  caption: string | null
  broadcast_price_cents: number | null
  unlocked: boolean | null
  media: CreatorMedia[]
  created_at: string
  updated_at: string
}

export interface FeedResponse {
  teaser: boolean
  posts: FeedPost[]
  page: number
  page_size: number
  total: number
  has_more: boolean
}

export interface SubscribeResult {
  subscription: SubscriptionInfo
  checkout_url: string | null
  status: string
}

export interface CreatorProfile {
  user_id: number
  display_name: string | null
  bio: string | null
  avatar_url: string | null
  social_links: Record<string, string> | null
  payout_info: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface SubscriptionInfo {
  id: number
  subscriber_id: number
  creator_id: number
  status: string
  current_period_start: string | null
  current_period_end: string | null
  payment_provider: string | null
  external_ref: string | null
  checkout_url: string | null
  cancel_at_period_end: boolean
  created_at: string
  updated_at: string
}

export interface SubscribeStatus {
  viewer_level: 'anonymous' | 'registered' | 'follower'
  subscription: SubscriptionInfo | null
  tier_price_cents: number
}

export interface UserSummary {
  id: number
  username: string | null
}

export interface ChatMessage {
  id: number
  conversation_id: number
  sender_id: number
  recipient_id: number
  body: string
  read_at: string | null
  created_at: string
}

export interface Conversation {
  id: number
  creator_id: number
  subscriber_id: number
  created_at: string
  updated_at: string
  other: UserSummary
  last_message: ChatMessage | null
}

export interface MessagesPage {
  messages: ChatMessage[]
  before_id: number | null
  has_more: boolean
}

export interface MessagesStatus {
  recipient_id: number
  recipient_username: string | null
  recipient_is_creator: boolean
  is_follower: boolean
  has_conversation: boolean
  messaging_enabled: boolean
  can_message: boolean
  reason: string
}

export const api = {
  login(email: string, password: string): Promise<TokenResponse> {
    return request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
  },

  logout(refreshToken: string): Promise<void> {
    return request<void>('/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
  },

  getGatewaySettings(): Promise<GatewaySettings[]> {
    return request<GatewaySettings[]>('/creator/gateway-settings')
  },

  updateGateway(
    gateway: string,
    payload: { enabled: boolean; config: Record<string, string> },
  ): Promise<GatewaySettings> {
    return request<GatewaySettings>(`/creator/gateway-settings/${gateway}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  getMessagingSettings(): Promise<MessagingSettings> {
    return request<MessagingSettings>('/creator/messaging-settings')
  },

  updateMessagingSettings(allow: boolean): Promise<MessagingSettings> {
    return request<MessagingSettings>('/creator/messaging-settings', {
      method: 'PUT',
      body: JSON.stringify({ allow_messages_from_all_followers: allow }),
    })
  },

  getCreatorContent(): Promise<CreatorPost[]> {
    return request<CreatorPost[]>('/creator/content')
  },

  updateCreatorPost(postId: number, payload: PostUpdate): Promise<CreatorPost> {
    return request<CreatorPost>(`/creator/content/${postId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  },

  deleteCreatorPost(postId: number): Promise<void> {
    return request<void>(`/creator/content/${postId}`, { method: 'DELETE' })
  },

  getCreatorSubscribers(
    page: number,
    pageSize: number,
    status?: string,
  ): Promise<SubscriberList> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    })
    if (status) params.set('status', status)
    return request<SubscriberList>(`/creator/subscribers?${params.toString()}`)
  },

  getCreatorLanding(creatorId: number): Promise<CreatorLanding> {
    return request<CreatorLanding>(`/creators/${creatorId}/landing`)
  },

  getCreatorProfile(): Promise<CreatorProfile> {
    return request<CreatorProfile>('/creator/profile')
  },

  updateCreatorProfile(
    payload: Partial<Pick<CreatorProfile, 'display_name' | 'bio' | 'avatar_url' | 'social_links'>>,
  ): Promise<CreatorProfile> {
    return request<CreatorProfile>('/creator/profile', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  getCreatorFeed(creatorId: number, page = 1, pageSize = 10): Promise<FeedResponse> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    })
    return request<FeedResponse>(`/creators/${creatorId}/posts?${params.toString()}`)
  },

  subscribe(
    creatorId: number,
    provider?: string,
  ): Promise<SubscribeResult> {
    return request<SubscribeResult>('/subscribe', {
      method: 'POST',
      body: JSON.stringify({ creator_id: creatorId, provider: provider ?? null }),
    })
  },

  getSubscribeStatus(creatorId: number): Promise<SubscribeStatus> {
    return request<SubscribeStatus>(
      `/subscribe/status?creator_id=${creatorId}`,
    )
  },

  getCreatorGateways(creatorId: number): Promise<LandingGateway[]> {
    return request<LandingGateway[]>(`/creators/${creatorId}/gateways`)
  },

  getConversations(): Promise<Conversation[]> {
    return request<Conversation[]>('/conversations')
  },

  getConversationMessages(
    conversationId: number,
    limit = 50,
    beforeId?: number,
  ): Promise<MessagesPage> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (beforeId != null) params.set('before_id', String(beforeId))
    return request<MessagesPage>(
      `/conversations/${conversationId}/messages?${params.toString()}`,
    )
  },

  sendMessage(recipientId: number, body: string): Promise<ChatMessage> {
    return request<ChatMessage>('/messages', {
      method: 'POST',
      body: JSON.stringify({ recipient_id: recipientId, body }),
    })
  },

  getMessagesStatus(recipientId: number): Promise<MessagesStatus> {
    return request<MessagesStatus>(`/messages/status?recipient_id=${recipientId}`)
  },

  unlockBroadcast(postId: number): Promise<{
    post_id: number
    broadcast_price_cents: number
    already_unlocked: boolean
    unlock: {
      id: number
      subscriber_id: number
      post_id: number
      payment_provider: string | null
      external_ref: string | null
      refunded_at: string | null
      created_at: string
    }
  }> {
    return request(`/content/${postId}/unlock`, { method: 'POST' })
  },
}
