import requests
import streamlit as st

DEFAULT_API_URL = "https://supervisor-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/copilot"

st.set_page_config(
    page_title="Supply Chain Multi-Agent Assistant",
    page_icon="🔗",
    layout="centered",
)

st.title("🔗 Supply Chain Multi-Agent Assistant")
st.caption("Multi-agent UI powered by Supervisor, Inventory and Supplier agents on Azure Container Apps.")

api_url = st.sidebar.text_input("Copilot endpoint", value=DEFAULT_API_URL)

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

question = st.chat_input("Ask a supply chain, inventory, or supplier question...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Calling Azure multi-agent system..."):
            try:
                response = requests.post(
                    api_url,
                    json={"question": question},
                    timeout=180,
                )
                response.raise_for_status()
                data = response.json()

                answer = data.get("answer", "No answer returned.")
                trace_id = data.get("trace_id")
                validation_passed = data.get("validation_passed")
                validation_reason = data.get("validation_reason")

                st.markdown(answer)

                with st.expander("Execution details"):
                    st.write("Trace ID:", trace_id)
                    st.write("Validation passed:", validation_passed)
                    st.write("Validation reason:", validation_reason)
                    st.json(data)

                st.session_state.messages.append(
                    {"role": "assistant", "content": answer}
                )

            except requests.exceptions.RequestException as exc:
                st.error(f"Request failed: {exc}")
