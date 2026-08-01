import { useCallback, useRef, useState } from 'react';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import PromptInput from '@cloudscape-design/components/prompt-input';
import ChatBubble from '@cloudscape-design/chat-components/chat-bubble';
import Avatar from '@cloudscape-design/chat-components/avatar';

// API 엔드포인트는 하드코딩하지 않고 .env.local의 VITE_API_URL에서 읽습니다.
const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');

export default function App() {
  const [messages, setMessages] = useState([]);
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(false);
  // 서버에서 반환된 sessionId를 유지해 멀티턴 대화를 이어갑니다.
  const sessionIdRef = useRef(null);

  const send = useCallback(async () => {
    const prompt = value.trim();
    if (!prompt || loading) return;
    setValue('');
    setMessages((prev) => [...prev, { role: 'user', text: prompt }]);
    setLoading(true);
    try {
      const body = { prompt };
      if (sessionIdRef.current) {
        body.sessionId = sessionIdRef.current;
      }
      const res = await fetch(`${API_URL}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (res.ok) {
        // 서버가 반환한 sessionId를 저장해 다음 요청에서 재사용합니다.
        if (data.sessionId) {
          sessionIdRef.current = data.sessionId;
        }
        setMessages((prev) => [
          ...prev,
          { role: 'ai', text: data.answer ?? '(빈 응답)' },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { role: 'ai', text: `오류: ${data.error ?? res.status}` },
        ]);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'ai', text: `요청 실패: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }, [value, loading]);

  const startNewChat = useCallback(() => {
    sessionIdRef.current = null;
    setMessages([]);
    setValue('');
  }, []);

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: 24 }}>
      <Container
        header={
          <Header
            variant="h1"
            description="AgentCore Runtime의 다이닝 에이전트와 대화합니다"
            actions={
              <Button
                variant="normal"
                iconName="refresh"
                onClick={startNewChat}
                disabled={loading}
              >
                새 대화
              </Button>
            }
          >
            강남 다이닝
          </Header>
        }
      >
        <SpaceBetween size="m">
          {messages.map((m, i) =>
            m.role === 'user' ? (
              <ChatBubble
                key={i}
                type="outgoing"
                ariaLabel={`사용자 메시지 ${i + 1}`}
                avatar={<Avatar ariaLabel="사용자" tooltipText="사용자" />}
              >
                {m.text}
              </ChatBubble>
            ) : (
              <ChatBubble
                key={i}
                type="incoming"
                ariaLabel={`에이전트 응답 ${i + 1}`}
                avatar={
                  <Avatar
                    color="gen-ai"
                    iconName="gen-ai"
                    ariaLabel="다이닝 에이전트"
                    tooltipText="다이닝 에이전트"
                  />
                }
              >
                {m.text}
              </ChatBubble>
            ),
          )}
          {loading && (
            <ChatBubble
              type="incoming"
              showLoadingBar
              ariaLabel="에이전트 응답 생성 중"
              avatar={
                <Avatar
                  loading
                  color="gen-ai"
                  iconName="gen-ai"
                  ariaLabel="다이닝 에이전트"
                />
              }
            >
              답변을 생성하는 중입니다…
            </ChatBubble>
          )}
          <PromptInput
            value={value}
            onChange={({ detail }) => setValue(detail.value)}
            onAction={send}
            actionButtonIconName="send"
            placeholder="식당을 물어보세요"
            i18nStrings={{ actionButtonAriaLabel: '메시지 전송' }}
          />
        </SpaceBetween>
      </Container>
    </div>
  );
}
