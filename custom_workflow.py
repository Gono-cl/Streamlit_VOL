import streamlit as st
import inspect
import importlib.util
import tempfile
import os
from streamlit_sortables import sort_items

st.title("🔧 Build Your Custom Workflow")

uploaded_file = st.file_uploader("Upload a Python file with functions", type=["py"])

if uploaded_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as tmp_file:
        tmp_file.write(uploaded_file.read())
        module_path = tmp_file.name

    spec = importlib.util.spec_from_file_location("user_module", module_path)
    user_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(user_module)
    st.write("Module loaded:", user_module)

    functions = {
        name: func for name, func in inspect.getmembers(user_module, inspect.isfunction)
    }

    st.success(f"Found {len(functions)} functions")
    st.write(list(functions.keys()))

    selected_funcs = st.multiselect("Select functions to include", list(functions.keys()))

    if selected_funcs:
        st.markdown("### 🧩 Reorder Selected Functions")
        safe_funcs = [str(f) for f in selected_funcs]
        ordered_funcs = sort_items(header="Function Order", items=safe_funcs)


        param_inputs = {}
        for name in ordered_funcs:
            func = functions[name]
            sig = inspect.signature(func)
            st.markdown(f"#### Parameters for `{name}()`")
            inputs = {}
            for param in sig.parameters.values():
                default = param.default if param.default is not inspect.Parameter.empty else ""
                inputs[param.name] = st.text_input(f"{name} - {param.name}", value=str(default))
            param_inputs[name] = inputs

        if st.button("🚀 Run Workflow"):
            results = []
            for name in ordered_funcs:
                func = functions[name]
                kwargs = {k: eval(v) for k, v in param_inputs[name].items() if v != ""}
                st.write(f"Running `{name}()` with {kwargs}")
                try:
                    result = func(**kwargs)
                    st.success(f"✅ {name}() returned: {result}")
                    results.append((name, result))
                except Exception as e:
                    st.error(f"❌ Error running {name}(): {e}")

            st.markdown("### 📝 Workflow Summary")
            for step, output in results:
                st.write(f"**{step}** → `{output}`")

    os.remove(module_path)

