"""UI helpers for ExperimentRunner (Streamlit placeholders)."""
from __future__ import annotations

import time


class UIMixin:
    def countdown(self, residence_time):
        for secs in range(residence_time * 9, 0, -1):
            mm, ss = secs // 60, secs % 60
            countdown_html = f"""
            <div style='background-color:#fff3cd; padding: 15px; border-left: 5px solid #ffca28; border-radius: 5px;'>
                <h4 style='margin:0;'>Countdown to Reach Steady State</h4>
                <p style='font-size: 24px; font-weight: bold; color: #856404; margin: 5px 0 0 0;'>{mm:02d}:{ss:02d}</p>
            </div>
            """
            self.countdown_placeholder.markdown(countdown_html, unsafe_allow_html=True)
            time.sleep(1)

    def display_experiment_info(self, experiment_number, total_iterations, parameters):
        elapsed = time.time() - self.start_time if self.start_time else 0
        mins, secs = divmod(int(elapsed), 60)

        self.timer_placeholder.markdown(f"Total Time Running: {mins:02d}:{secs:02d}")

        html = f"""
        <div style='background-color:#eef6fb; padding: 10px; border-left: 5px solid #2c91c6;'>
            <h4 style='margin:0;'>Experiment {experiment_number} of {total_iterations}</h4>
            <p style='margin:5px 0 10px 0;'>Elapsed Time: {mins:02d}:{secs:02d}</p>
            <ul style='padding-left: 20px;'>
        """
        for key, val in parameters.items():
            try:
                html += f"<li><strong>{key}</strong>: {float(val):.2f}</li>"
            except (TypeError, ValueError):
                html += f"<li><strong>{key}</strong>: {val}</li>"
        html += "</ul></div>"
        self.experiment_status_placeholder.markdown(html, unsafe_allow_html=True)

    def countdown_echem(self, flow_rate):
        residence_time = self.calculate_residence_time(flow_rate)
        residence_time = int(residence_time)
        for secs in range(residence_time, 0, -1):
            mm, ss = secs // 60, secs % 60
            countdown_html = f"""
            <div style='background-color:#fff3cd; padding: 15px; border-left: 5px solid #ffca28; border-radius: 5px;'>
                <h4 style='margin:0;'>Countdown to Reach Steady State</h4>
                <p style='font-size: 24px; font-weight: bold; color: #856404; margin: 5px 0 0 0;'>{mm:02d}:{ss:02d}</p>
            </div>
            """
            self.countdown_placeholder.markdown(countdown_html, unsafe_allow_html=True)
            time.sleep(1)

    def time_for_measurements(self, flow_rate):
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 1)
        print("Valve switched to measurement position.")

        downstream_vol = 1.0
        time_until_collection = (downstream_vol / flow_rate) * 60
        time_until_collection = int(time_until_collection)

        for secs in range(time_until_collection, 0, -1):
            mm, ss = secs // 60, secs % 60
            countdown_html = f"""
            <div style='background-color:#fff3cd; padding: 15px; border-left: 5px solid #ffca28; border-radius: 5px;'>
                <h4 style='margin:0;'>Countdown to collect measurements </h4>
                <p style='font-size: 24px; font-weight: bold; color: #856404; margin: 5px 0 0 0;'>{mm:02d}:{ss:02d}</p>
            </div>
            """
            self.countdown_placeholder.markdown(countdown_html, unsafe_allow_html=True)
            time.sleep(1)
