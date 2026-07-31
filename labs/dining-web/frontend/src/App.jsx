import { useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import PromptInput from '@cloudscape-design/components/prompt-input';
import ChatBubble from '@cloudscape-design/chat-components/chat-bubble';
import Avatar from '@cloudscape-design/chat-components/avatar';

// API 엔드포인트는 하드코딩하지 않고 .env.local의 VITE_API_URL에서 읽습니다.
// 뒤 슬래시가 있으면 `${API_URL}/ask`가 이중 슬래시(//ask)가 되므로 정규화합니다.
const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');

export default function App() {
  const [messages, setMessages] = useState([]);
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(false);

  const send = async () => {
    const prompt = value.trim();
    if (!prompt || loading) return;
    setValue('');
    setMessages((prev) => [...prev, { role: 'user', text: prompt }]);
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      const data = await res.json();
      const text = res.ok
        ? (data.answer ?? '(빈 응답)')
        : `오류: ${data.error ?? res.status}`;
      setMessages((prev) => [...prev, { role: 'ai', text }]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'ai', text: `요청 실패: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: 24 }}>
      <Container
        header={
          <Header
            variant="h1"
            description="AgentCore Runtime의 다이닝 에이전트와 대화합니다"
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
