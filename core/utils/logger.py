class StreamlitLogger:
    def __init__(self, placeholder):
        self.placeholder = placeholder
        self.logs = []

    def write(self, message):
        if message.strip():  # Avoid printing empty lines
            self.logs.append(message)
        log_html = (
            "<div id='log-box' style='height: 300px; overflow-y: auto; background-color: #f9f9f9; "
            "padding: 10px; border: 1px solid #ccc; font-family: monospace; font-size: 14px;'>"
            + "<br>".join(self.logs[-100:]) +
            "</div>"
            "<script>var box = document.getElementById('log-box'); box.scrollTop = box.scrollHeight;</script>"
        )
        self.placeholder.markdown(log_html, unsafe_allow_html=True)

    def flush(self):
        pass

