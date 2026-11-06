import { useRef, useEffect } from 'react';
import { ChatMessage } from '@/components/ChatMessage';
import { ChatInput } from '@/components/ChatInput';
import { useChat } from '@/hooks/useChat';
import { BarChart3, Loader2, Plus, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';

const TITANIC_SUGGESTIONS = [
  'Show the survival rate by passenger class as a bar chart',
  'What is the average fare and age by sex?',
  'Plot the age distribution of passengers',
];

const DATA_SUGGESTIONS = [
  'Summarize this dataset',
  'Show the first 10 rows',
  'Chart the distribution of the most interesting column',
];

const GENERAL_SUGGESTIONS = [
  'Explain overfitting in simple terms',
  'What can you do with a CSV file?',
];

const Index = () => {
  const {
    messages,
    isLoading,
    sessionInfo,
    sendMessage,
    uploadFile,
    clearSession,
    clearMessages,
  } = useChat();

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  const loadSample = async () => {
    try {
      const res = await fetch('/samples/titanic.csv');
      if (!res.ok) throw new Error();
      const blob = await res.blob();
      await uploadFile(new File([blob], 'Titanic-Dataset.csv', { type: 'text/csv' }));
    } catch {
      toast.error('Could not load the sample dataset.');
    }
  };

  const hasMessages = messages.length > 0;
  const suggestions = !sessionInfo
    ? GENERAL_SUGGESTIONS
    : /titanic/i.test(sessionInfo.filename)
      ? TITANIC_SUGGESTIONS
      : DATA_SUGGESTIONS;

  const input = (
    <ChatInput
      onSendMessage={sendMessage}
      onUploadFile={uploadFile}
      isLoading={isLoading}
      dataset={sessionInfo}
      onClearFile={clearSession}
    />
  );

  return (
    <div className="flex flex-col h-screen bg-background">
      <header className="border-b border-border px-4 py-3">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-primary text-primary-foreground flex items-center justify-center">
              <BarChart3 className="w-4 h-4" />
            </div>
            <span className="font-semibold text-foreground">CSV Insight Agent</span>
          </div>
          {hasMessages && (
            <Button variant="outline" size="sm" onClick={clearMessages} className="gap-2">
              <Plus className="w-4 h-4" />
              New chat
            </Button>
          )}
        </div>
      </header>

      <main className="flex-1 flex flex-col overflow-hidden">
        {!hasMessages ? (
          <div className="flex-1 flex flex-col items-center justify-center px-4">
            <h1 className="text-3xl font-semibold text-foreground mb-2 text-center">
              Ask your data anything
            </h1>
            <p className="text-muted-foreground mb-8 text-center max-w-md">
              Upload a CSV and the agent writes SQL, analyzes the results and draws charts.
              Or just ask a question.
            </p>
            {input}
            <div className="mt-5 flex flex-wrap justify-center gap-2 max-w-2xl">
              {!sessionInfo && (
                <button
                  type="button"
                  onClick={loadSample}
                  disabled={isLoading}
                  className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-full bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  Try the Titanic sample
                </button>
              )}
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => sendMessage(s)}
                  disabled={isLoading}
                  className="text-sm px-3 py-1.5 rounded-full border border-border text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-50 transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="flex-1 overflow-y-auto px-4 py-6">
              <div className="max-w-3xl mx-auto space-y-4">
                {messages.map((message) => (
                  <ChatMessage
                    key={message.id}
                    role={message.role}
                    content={message.content}
                    chartUrls={message.chartUrls}
                  />
                ))}
                {isLoading && (
                  <div className="flex justify-start">
                    <div className="bg-chat-assistant rounded-2xl px-4 py-3 flex items-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {sessionInfo ? 'Querying and analyzing your data…' : 'Thinking…'}
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>
            </div>

            <div className="border-t border-border p-4">{input}</div>
          </>
        )}
      </main>
    </div>
  );
};

export default Index;
