import React from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource-variable/dm-sans'
import '@fontsource-variable/newsreader'
import App from './App'
import './styles.css'

document.documentElement.dataset.buildRevision = import.meta.env.VITE_BUILD_REVISION || 'local'

class ErrorBoundary extends React.Component<React.PropsWithChildren, { error: boolean }> {
  state = { error: false }
  static getDerivedStateFromError() {
    return { error: true }
  }
  render() {
    if (this.state.error)
      return (
        <main className="startup">
          <h1>Let’s get your workspace back.</h1>
          <p>
            The interface could not finish loading. Reload to try again; your saved project stays in
            this browser.
          </p>
          <button onClick={() => location.reload()}>Reload workspace</button>
        </main>
      )
    return this.props.children
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
