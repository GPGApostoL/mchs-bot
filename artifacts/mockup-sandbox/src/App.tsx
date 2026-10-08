import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, Check, ChevronRight, CircleHelp, RotateCcw, Sparkles, X } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_URL || "";

interface Question {
  question: string;
  options: string[];
  answer_inferred?: boolean;
}

interface StartResponse {
  session_id: string;
  total: number;
  user?: { first_name?: string; username?: string } | null;
  question_index: number;
  question: Question;
}

interface AnswerResponse {
  correct: boolean;
  correct_index: number;
  correct_answer: string;
  question_index: number;
  finished: boolean;
  next_question_index: number | null;
  next_question: Question | null;
  score?: number;
  total?: number;
  percent?: number;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: any };
  }
}

function tg() {
  return window.Telegram?.WebApp;
}

function App() {
  const [screen, setScreen] = useState<"home" | "quiz" | "result">("home");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState<Question | null>(null);
  const [questionIndex, setQuestionIndex] = useState(0);
  const [total, setTotal] = useState(50);
  const [score, setScore] = useState(0);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const webApp = tg();
    webApp?.ready?.();
    webApp?.expand?.();
    if (webApp?.setHeaderColor) webApp.setHeaderColor("#070807");
    if (webApp?.setBackgroundColor) webApp.setBackgroundColor("#070807");
  }, []);

  const percent = total ? Math.round((questionIndex / total) * 100) : 0;
  const displayedScore = answer?.finished ? answer.score ?? score : score;
  const userName = useMemo(() => {
    const user = tg()?.initDataUnsafe?.user;
    return user?.first_name || user?.username || "коллега";
  }, []);

  async function startQuiz() {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/quiz/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: tg()?.initData || "" }),
      });
      if (!response.ok) throw new Error("Не удалось запустить тест.");
      const data: StartResponse = await response.json();
      setSessionId(data.session_id);
      setTotal(data.total);
      setQuestionIndex(data.question_index);
      setQuestion(data.question);
      setScore(0);
      setSelected(null);
      setAnswer(null);
      setScreen("quiz");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка запуска теста.");
    } finally {
      setBusy(false);
    }
  }

  async function chooseAnswer(index: number) {
    if (!sessionId || !question || busy || answer) return;
    setSelected(index);
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/quiz/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, question_index: questionIndex, option_index: index }),
      });
      if (!response.ok) throw new Error("Не удалось сохранить ответ.");
      const data: AnswerResponse = await response.json();
      setAnswer(data);
      if (data.correct) setScore((value) => value + 1);
      if (data.finished) {
        setTimeout(() => setScreen("result"), 650);
      } else {
        setTimeout(() => {
          setQuestion(data.next_question);
          setQuestionIndex(data.next_question_index ?? questionIndex + 1);
          setSelected(null);
          setAnswer(null);
          setBusy(false);
        }, 700);
      }
    } catch (e) {
      setSelected(null);
      setError(e instanceof Error ? e.message : "Ошибка ответа.");
      setBusy(false);
    }
  }

  function backHome() {
    setScreen("home");
    setQuestion(null);
    setAnswer(null);
    setSelected(null);
    setSessionId(null);
    setError(null);
  }

  if (screen === "result") {
    const finalScore = answer?.score ?? score;
    const finalPercent = answer?.percent ?? Math.round((finalScore / total) * 100);
    return (
      <main className="app-shell">
        <div className="glow glow-result" />
        <section className="result-screen">
          <div className="eyebrow"><Sparkles size={15} /> РЕЗУЛЬТАТ ТЕСТИРОВАНИЯ</div>
          <div className="result-number">{finalPercent}<span>%</span></div>
          <h1>{finalPercent >= 75 ? "Тест пройден" : "Нужно повторить"}</h1>
          <p className="muted">{finalScore} правильных ответов из {total}</p>

          <div className="result-card">
            <div><span>Правильных</span><strong>{finalScore}</strong></div>
            <div><span>Ошибочных</span><strong>{total - finalScore}</strong></div>
            <div><span>Всего</span><strong>{total}</strong></div>
          </div>

          <button className="primary-button" onClick={startQuiz} disabled={busy}>
            <RotateCcw size={18} /> Пройти ещё раз
          </button>
          <button className="ghost-button" onClick={backHome}>На главный экран</button>
        </section>
      </main>
    );
  }

  if (screen === "quiz" && question) {
    return (
      <main className="app-shell quiz-shell">
        <header className="quiz-header">
          <button className="icon-button" onClick={backHome} aria-label="Назад"><ArrowLeft size={19} /></button>
          <div className="quiz-meta">
            <span>ТЕСТИРОВАНИЕ</span>
            <strong>{String(questionIndex + 1).padStart(2, "0")} / {total}</strong>
          </div>
          <div className="score-pill">{score}</div>
        </header>

        <div className="progress-track"><div className="progress-fill" style={{ width: `${Math.max(2, percent)}%` }} /></div>

        <section className="question-area">
          <div className="question-tag"><CircleHelp size={15} /> ВОПРОС</div>
          <h1>{question.question}</h1>
          {question.answer_inferred && <div className="note">Ответ в исходном документе был отмечен как требующий уточнения.</div>}

          <div className="answers">
            {question.options.map((option, index) => {
              const label = String.fromCharCode(65 + index);
              const isSelected = selected === index;
              const isCorrect = answer?.correct_index === index;
              const isWrong = answer && isSelected && !answer.correct;
              return (
                <button
                  key={`${questionIndex}-${index}`}
                  className={`answer-card ${isSelected ? "selected" : ""} ${isCorrect ? "correct" : ""} ${isWrong ? "wrong" : ""}`}
                  onClick={() => chooseAnswer(index)}
                  disabled={busy || !!answer}
                >
                  <span className="answer-label">{label}</span>
                  <span className="answer-text">{option}</span>
                  <span className="answer-mark">
                    {isCorrect ? <Check size={18} /> : isWrong ? <X size={18} /> : <ChevronRight size={18} />}
                  </span>
                </button>
              );
            })}
          </div>

          {answer && !answer.finished && (
            <div className={`feedback ${answer.correct ? "good" : "bad"}`}>
              {answer.correct ? <Check size={17} /> : <X size={17} />}
              {answer.correct ? "Правильно" : `Неверно. Правильный ответ: ${answer.correct_answer}`}
            </div>
          )}
        </section>

        {error && <div className="error-box">{error}</div>}
      </main>
    );
  }

  return (
    <main className="app-shell home-shell">
      <div className="glow glow-one" /><div className="glow glow-two" />
      <header className="brand-row">
        <div className="brand-mark">ЛЧС</div>
        <span className="brand-caption">QUALIFICATION / TEST</span>
      </header>

      <section className="hero">
        <div className="eyebrow"><Sparkles size={15} /> МИНИ-ПРИЛОЖЕНИЕ</div>
        <h1>Проверь<br /><span>знания.</span></h1>
        <p>Интерактивное квалификационное тестирование по материалам твоего исходного документа.</p>

        <div className="stats-row">
          <div><strong>311</strong><span>в базе</span></div>
          <div><strong>50</strong><span>за тест</span></div>
          <div><strong>4</strong><span>варианта</span></div>
        </div>

        <button className="primary-button start-button" onClick={startQuiz} disabled={busy}>
          {busy ? "Запускаем…" : <>Начать тест <ArrowRight size={20} /></>}
        </button>
        <p className="hello">Привет, {userName}.</p>
        {error && <div className="error-box">{error}</div>}
      </section>

      <footer className="home-footer">Ответы выбираются нажатием на всю карточку — быстро и удобно с телефона.</footer>
    </main>
  );
}

export default App;
