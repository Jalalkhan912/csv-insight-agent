import { useState, useCallback, useRef } from 'react';
import { toast } from 'sonner';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  chartUrls?: string[];
}

interface ChatResponse {
  session_id: string | null;
  response: string;
  visualization_code: string | null;
  chart_urls?: string[] | null;
  mode: string;
}

export interface SessionInfo {
  session_id: string;
  filename: string;
  columns: string[];
  row_count: number;
}

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/** Pull FastAPI's `detail` message out of an error response, if there is one. */
async function errorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    return typeof body?.detail === 'string' ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

function deleteServerSession(id: string | null) {
  if (!id) return;
  // Best effort: the server keeps sessions in memory, so a failure here is harmless.
  fetch(`${API_BASE_URL}/session/${id}`, { method: 'DELETE' }).catch(() => {});
}

let nextId = 0;
const newId = () => `${Date.now()}-${nextId++}`;

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionInfo, setSessionInfo] = useState<SessionInfo | null>(null);
  // Bumped on "New chat" so replies to an abandoned conversation are ignored.
  const conversation = useRef(0);

  const uploadFile = useCallback(async (file: File): Promise<SessionInfo | null> => {
    if (!file.name.toLowerCase().endsWith('.csv')) {
      toast.error('Only CSV files are supported.');
      return null;
    }

    setIsLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch(`${API_BASE_URL}/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error(await errorDetail(response, 'Failed to upload file.'));
      }

      const data: SessionInfo = await response.json();
      setSessionId(data.session_id);
      setSessionInfo(data);
      toast.success(`Loaded ${data.filename}`, {
        description: `${data.row_count.toLocaleString()} rows · ${data.columns.length} columns`,
      });
      return data;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to upload file.';
      toast.error('Upload failed', {
        description: message === 'Failed to fetch' ? `Can't reach the API at ${API_BASE_URL}.` : message,
      });
      return null;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const sendMessage = useCallback(async (content: string) => {
    const conversationAtSend = conversation.current;
    setMessages((prev) => [...prev, { id: newId(), role: 'user', content }]);
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message: content }),
      });

      if (!response.ok) {
        throw new Error(await errorDetail(response, 'Failed to send message.'));
      }

      const data: ChatResponse = await response.json();
      if (conversation.current !== conversationAtSend) return;

      const chartUrls = data.chart_urls?.length
        ? data.chart_urls.map((url) => `${API_BASE_URL}${url}`)
        : undefined;

      setMessages((prev) => [
        ...prev,
        { id: newId(), role: 'assistant', content: data.response, chartUrls },
      ]);

      if (data.session_id) {
        setSessionId(data.session_id);
      }
    } catch (error) {
      if (conversation.current !== conversationAtSend) return;
      const message = error instanceof Error ? error.message : '';
      const content =
        message === 'Failed to fetch' || !message
          ? `Sorry, I couldn't reach the API server at ${API_BASE_URL}. Is it running?`
          : `Sorry, something went wrong: ${message}`;
      setMessages((prev) => [...prev, { id: newId(), role: 'assistant', content }]);
    } finally {
      if (conversation.current === conversationAtSend) setIsLoading(false);
    }
  }, [sessionId]);

  /** Detach the uploaded dataset but keep the visible conversation. */
  const clearSession = useCallback(() => {
    deleteServerSession(sessionId);
    setSessionId(null);
    setSessionInfo(null);
  }, [sessionId]);

  /** Start over: new conversation, no dataset. */
  const clearMessages = useCallback(() => {
    conversation.current += 1;
    deleteServerSession(sessionId);
    setMessages([]);
    setSessionId(null);
    setSessionInfo(null);
    setIsLoading(false);
  }, [sessionId]);

  return {
    messages,
    isLoading,
    sessionId,
    sessionInfo,
    sendMessage,
    uploadFile,
    clearSession,
    clearMessages,
  };
}
