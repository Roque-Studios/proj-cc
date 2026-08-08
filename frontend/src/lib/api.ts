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

export interface UserMe {
  id: number
  email: string
  username: string | null
  role: string
  is_creator: boolean
  is_active: boolean
}

export interface GatewayField {
  name: string
  label: string
  required: boolean
  secret: boolean
  placeholder: string
  options: string[]
  configured: boolean
  // Stored value echoed for NON-secret fields only (e.g. the environment
  // select) — secrets always come back null, so the form can pre-fill what's
  // safe to show without ever receiving the keys.
  value?: string | null
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
  // The real (auth-gated, watermarked) url — null when withheld.
  media_url: string | null
  // Blurred public preview url — set exactly when media_url is withheld, so
  // non-followers see the post's shape without the real bytes.
  preview_url: string | null
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
  like_count: number
  comment_count: number
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
  banner_url: string | null
  post_count: number
  // True while the creator has a live (unexpired) 24-hour story — turns the
  // avatar indicator green (MSN-online style). Public signal; the story
  // *content* stays follower-only.
  has_active_story?: boolean
  // The effective legal documents (creator's own or the platform defaults).
  tos_text: string | null
  privacy_text: string | null
  // The creator's monthly subscription price in cents (null = the platform
  // default) — lets the checkout show the real price.
  tier_price_cents: number | null
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
  // Engagement totals (public on every post) + the viewer's own like state.
  like_count: number
  comment_count: number
  liked_by_me: boolean
  created_at: string
  updated_at: string
}

export interface PostComment {
  id: number
  post_id: number
  user_id: number
  body: string
  author_username: string | null
  author_display_name: string | null
  author_avatar_url: string | null
  author_is_creator: boolean
  created_at: string
}

export interface CommentsPage {
  items: PostComment[]
  page: number
  page_size: number
  total: number
  has_more: boolean
}

export interface PostLikeResponse {
  post_id: number
  liked: boolean
  like_count: number
}

export interface FeedResponse {
  teaser: boolean
  posts: FeedPost[]
  page: number
  page_size: number
  total: number
  has_more: boolean
}

export interface MediaGalleryItem {
  media_id: number
  post_id: number
  media_type: string
  // The real watermarked url when accessible — null when withheld (locked
  // paid broadcast, or any non-follower item).
  media_url: string | null
  // Blurred public preview — set exactly when media_url is withheld.
  preview_url: string | null
  broadcast_price_cents: number | null
  unlocked: boolean | null
  post_caption: string | null
  created_at: string
}

export interface MediaGallery {
  teaser: boolean
  items: MediaGalleryItem[]
  page: number
  page_size: number
  total: number
  has_more: boolean
}

export interface StoryMedia {
  id: number
  media_type: string
  media_url: string | null
  created_at: string
}

export interface Story {
  id: number
  creator_id: number
  caption: string | null
  expires_at: string
  created_at: string
  media: StoryMedia[]
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
  banner_url: string | null
  social_links: Record<string, string> | null
  payout_info: Record<string, unknown> | null
  // The effective legal documents (creator's own text or the platform
  // defaults) — edited from the admin Legal tab, shown pre-checkout.
  tos_text: string | null
  privacy_text: string | null
  // The creator's own monthly subscription price in cents (admin Settings
  // tab); null = the platform default.
  tier_price_cents: number | null
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
  // The monthly price (cents) snapshotted at checkout.
  tier_price_cents: number | null
  created_at: string
  updated_at: string
}

export interface MySubscription {
  subscription_id: number
  creator_id: number
  creator_username: string | null
  creator_display_name: string | null
  status: string
  current_period_start: string | null
  current_period_end: string | null
  cancel_at_period_end: boolean
  payment_provider: string | null
  created_at: string
  // Whole days left in the current billing period (null when not active).
  days_left: number | null
}

export interface MySubscriptions {
  items: MySubscription[]
}

export interface SubscribeStatus {
  viewer_level: 'anonymous' | 'registered' | 'follower'
  subscription: SubscriptionInfo | null
  tier_price_cents: number
}

export interface UserSummary {
  id: number
  username: string | null
  /** Creator avatar url (subscribers have none — the UI shows initials). */
  avatar_url?: string | null
}

export interface ChatMessage {
  id: number
  conversation_id: number
  sender_id: number
  recipient_id: number
  body: string
  price_cents: number | null
  media: { id: number; message_id: number; media_type: string; media_url: string }[]
  unlocked: boolean | null
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

  register(email: string, password: string, username?: string): Promise<UserMe> {
    return request<UserMe>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password, username: username || null }),
    })
  },

  me(): Promise<UserMe> {
    return request<UserMe>('/auth/me')
  },

  changePassword(currentPassword: string, newPassword: string): Promise<void> {
    return request<void>('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    })
  },

  forgotPassword(email: string): Promise<{ sent: boolean; dev_token?: string | null }> {
    return request<{ sent: boolean; dev_token?: string | null }>('/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify({ email }),
    })
  },

  resetPassword(token: string, newPassword: string): Promise<void> {
    return request<void>('/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify({ token, new_password: newPassword }),
    })
  },

  // The authenticated user's own subscriptions (profile page: days left).
  getMySubscriptions(): Promise<MySubscriptions> {
    return request<MySubscriptions>('/me/subscriptions')
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

  createCreatorPost(
    files: File[],
    caption?: string,
    priceCents?: number,
  ): Promise<CreatorPost> {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    const trimmed = (caption ?? '').trim()
    if (trimmed) form.append('caption', trimmed)
    if (priceCents != null && priceCents > 0) {
      form.append('price_cents', String(priceCents))
    }
    return request<CreatorPost>('/posts', { method: 'POST', body: form })
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

  // The first (seed) creator — the site-root landing default.
  getDefaultLanding(): Promise<CreatorLanding> {
    return request<CreatorLanding>('/creators/default/landing')
  },

  getCreatorProfile(): Promise<CreatorProfile> {
    return request<CreatorProfile>('/creator/profile')
  },

  updateCreatorProfile(
    payload: Partial<
      Pick<
        CreatorProfile,
        | 'display_name'
        | 'bio'
        | 'avatar_url'
        | 'banner_url'
        | 'social_links'
        | 'tos_text'
        | 'privacy_text'
        | 'tier_price_cents'
      >
    >,
  ): Promise<CreatorProfile> {
    return request<CreatorProfile>('/creator/profile', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },

  uploadCreatorBanner(file: File): Promise<CreatorProfile> {
    const form = new FormData()
    form.append('file', file)
    return request<CreatorProfile>('/creator/banner', {
      method: 'POST',
      body: form,
    })
  },

  deleteCreatorBanner(): Promise<CreatorProfile> {
    return request<CreatorProfile>('/creator/banner', { method: 'DELETE' })
  },

  uploadCreatorAvatar(file: File): Promise<CreatorProfile> {
    const form = new FormData()
    form.append('file', file)
    return request<CreatorProfile>('/creator/avatar', {
      method: 'POST',
      body: form,
    })
  },

  deleteCreatorAvatar(): Promise<CreatorProfile> {
    return request<CreatorProfile>('/creator/avatar', { method: 'DELETE' })
  },

  // ---- 24-hour stories ----

  createStory(files: File[], caption?: string): Promise<Story> {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    const trimmed = (caption ?? '').trim()
    if (trimmed) form.append('caption', trimmed)
    return request<Story>('/stories', { method: 'POST', body: form })
  },

  // Follower-gated: the creator's live (unexpired) stories.
  getCreatorStories(creatorId: number): Promise<Story[]> {
    return request<Story[]>(`/stories/${creatorId}`)
  },

  // Creator dashboard: every own story, expired ones included.
  getCreatorOwnStories(): Promise<Story[]> {
    return request<Story[]>('/creator/stories')
  },

  deleteStory(storyId: number): Promise<void> {
    return request<void>(`/creator/stories/${storyId}`, { method: 'DELETE' })
  },

  getCreatorFeed(creatorId: number, page = 1, pageSize = 10): Promise<FeedResponse> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    })
    return request<FeedResponse>(`/creators/${creatorId}/posts?${params.toString()}`)
  },

  // Flat media gallery of a creator's full content (the MEDIA tab).
  getCreatorMedia(creatorId: number, page = 1, pageSize = 30): Promise<MediaGallery> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    })
    return request<MediaGallery>(`/creators/${creatorId}/media?${params.toString()}`)
  },

  // ---- Post engagement (likes + comments) ----

  likePost(postId: number): Promise<PostLikeResponse> {
    return request<PostLikeResponse>(`/posts/${postId}/like`, { method: 'POST' })
  },

  unlikePost(postId: number): Promise<PostLikeResponse> {
    return request<PostLikeResponse>(`/posts/${postId}/like`, { method: 'DELETE' })
  },

  getPostComments(postId: number, page = 1, pageSize = 20): Promise<CommentsPage> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    })
    return request<CommentsPage>(`/posts/${postId}/comments?${params.toString()}`)
  },

  createPostComment(postId: number, body: string): Promise<PostComment> {
    return request<PostComment>(`/posts/${postId}/comments`, {
      method: 'POST',
      body: JSON.stringify({ body }),
    })
  },

  deletePostComment(postId: number, commentId: number): Promise<void> {
    return request<void>(`/posts/${postId}/comments/${commentId}`, {
      method: 'DELETE',
    })
  },

  subscribe(
    creatorId: number,
    provider?: string,
    successUrl?: string,
    cancelUrl?: string,
    acceptedTos = false,
    ageConfirmed = false,
  ): Promise<SubscribeResult> {
    return request<SubscribeResult>('/subscribe', {
      method: 'POST',
      body: JSON.stringify({
        creator_id: creatorId,
        provider: provider ?? null,
        // The hosted gateway redirects the customer back here after paying
        // (Wompi payment links use this as their urlRedirect).
        success_url: successUrl ?? null,
        cancel_url: cancelUrl ?? null,
        // Consent gate — both must be confirmed before any payment starts
        // (the backend rejects otherwise).
        accepted_tos: acceptedTos,
        age_confirmed: ageConfirmed,
      }),
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

  unlockBroadcast(
    postId: number,
    returnUrls?: { success_url?: string; cancel_url?: string },
  ): Promise<{
    post_id: number
    broadcast_price_cents: number
    already_unlocked: boolean
    checkout_url: string | null
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
    const q = new URLSearchParams()
    if (returnUrls?.success_url) q.set('success_url', returnUrls.success_url)
    if (returnUrls?.cancel_url) q.set('cancel_url', returnUrls.cancel_url)
    const suffix = q.toString() ? `?${q.toString()}` : ''
    return request(`/content/${postId}/unlock${suffix}`, { method: 'POST' })
  },

  sendMessageWithMedia(
    recipientId: number,
    body: string,
    files: File[],
    priceCents?: number | null,
  ): Promise<ChatMessage> {
    const form = new FormData()
    form.set('recipient_id', String(recipientId))
    form.set('body', body)
    if (priceCents != null) form.set('price_cents', String(priceCents))
    for (const file of files) form.append('files', file)
    return request<ChatMessage>('/messages/media', {
      method: 'POST',
      body: form,
    })
  },

  unlockMessage(
    messageId: number,
    returnUrls?: { success_url?: string; cancel_url?: string },
  ): Promise<{
    message_id: number
    price_cents: number
    already_unlocked: boolean
    checkout_url: string | null
  }> {
    const q = new URLSearchParams()
    if (returnUrls?.success_url) q.set('success_url', returnUrls.success_url)
    if (returnUrls?.cancel_url) q.set('cancel_url', returnUrls.cancel_url)
    const suffix = q.toString() ? `?${q.toString()}` : ''
    return request(`/messages/${messageId}/unlock${suffix}`, { method: 'POST' })
  },
}
