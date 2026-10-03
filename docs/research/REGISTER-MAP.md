# S3 register map from the PHY libraries (static, inferred)

Generated from `esp-phy-lib` `libphy.a` + `librftest.a` (esp32s3), disassembled with the ESP toolchain's `xtensa-esp32s3-elf-objdump -dr`. Each line is a
literal-pool address that a function loads; it says *which function touches the register*, not what the bits mean. Bit meanings must be confirmed on hardware.
Start with the MAC/dump block: `mac_common:dactrig` (DAC playback from SRAM) uses `0x60033D64`, see [TX-RESEARCH.md](TX-RESEARCH.md#stage-5-dactrig).


## Frontend (0x6000_6xxx)

| Register | Used by |
|---|---|
| `0x60006000` | `phy_debug:phy_reg_check`, `phy_feature:phy_txtone_start`, `phy_feature:phy_txtone_stop`, `phy_reg:iram1`, `phy_reg:start_tx_tone_step`, `phy_reg:stop_tx_tone` (+2) |
| `0x60006004` | `rf_test:wifiscwout` |
| `0x60006014` | `phy_reg:iram1` |
| `0x60006018` | `phy_reg:iram1` |
| `0x6000601C` | `phy_reg:iram1` |
| `0x60006020` | `phy_reg:iram1` |
| `0x60006024` | `phy_reg:iram1` |
| `0x60006028` | `phy_reg:iram1` |
| `0x6000602C` | `phy_reg:iram1` |
| `0x60006030` | `phy_reg:iram1` |
| `0x60006040` | `phy_pwdet:pwdet_tone_start`, `phy_reg:start_tx_tone`, `phy_reg:start_tx_tone_step`, `phy_reg:stop_tx_tone`, `phy_tx_cal:rfcal_pwrctrl`, `phy_tx_cal:rfcal_txiq` (+1) |
| `0x60006044` | `phy_reg:start_tx_tone_step`, `phy_reg:stop_tx_tone` |
| `0x6000604C` | `phy_reg:stop_tx_tone` |
| `0x60006050` | `phy_reg:start_tx_tone_step`, `phy_tx_cal:txiq_get_mis_pwr` |
| `0x60006064` | `phy_reg:phy_freq_correct` |
| `0x60006068` | `phy_reg:phy_freq_correct` |
| `0x60006070` | `phy_reg:phy_freq_correct`, `phy_reg:phy_get_fetx_delay` |
| `0x60006078` | `phy_analog_cal:phy_analog_delay_cal` |
| `0x6000607C` | `phy_analog_cal:phy_analog_delay_cal`, `phy_reg:rxiq_set_reg`, `phy_reg:txiq_set_reg`, `phy_rx_cal:rfcal_rxiq`, `phy_rx_cal:set_rx_gain_cal_iq`, `phy_rx_gain:set_rx_gain_param` (+4) |
| `0x60006088` | `phy_reg:iram1` |
| `0x60006090` | `phy_reg:phy_freq_correct`, `phy_reg:phy_get_fetx_delay` |
| `0x600060B0` | `phy_feature:ant_wifirx_cfg`, `phy_feature:ant_wifitx_cfg`, `phy_reg:iram1`, `phy_reg:tx_state_set` |
| `0x600060B4` | `phy_feature:ant_bttx_cfg`, `phy_feature:ant_wifirx_cfg`, `phy_reg:iram1`, `phy_reg:tx_state_set` |
| `0x600060B8` | `phy_feature:ant_btrx_cfg`, `phy_feature:ant_bttx_cfg`, `phy_reg:iram1`, `phy_reg:tx_state_set` |
| `0x600060BC` | `phy_feature:ant_btrx_cfg`, `phy_reg:iram1`, `phy_reg:tx_state_set` |
| `0x600060C8` | `phy_pbus:set_pbus_mem`, `wifi:pa_pbus_set`, `wifi:rx_pbus_set`, `wifi:set_pbus_mem_debug`, `wifi:tx_pbus_set` |
| `0x600060CC` | `phy_pbus:set_pbus_mem`, `wifi:pa_pbus_set`, `wifi:rx_pbus_set`, `wifi:set_pbus_mem_debug`, `wifi:tx_pbus_set` |
| `0x600060E0` | `phy_pbus:save_pbus_reg`, `phy_pbus:set_pbus_mem`, `phy_reg:iram1`, `wifi:rx_pbus_set`, `wifi:set_pbus_mem_debug` |
| `0x600060E4` | `phy_pbus:save_pbus_reg`, `phy_pbus:set_pbus_mem`, `phy_reg:iram1`, `wifi:set_pbus_mem_debug`, `wifi:tx_pbus_set` |
| `0x600060E8` | `phy_pbus:save_pbus_reg`, `phy_pbus:set_pbus_mem`, `phy_reg:iram1`, `wifi:pa_pbus_set`, `wifi:set_pbus_mem_debug` |
| `0x600060EC` | `phy_pbus:save_pbus_reg`, `phy_pbus:set_pbus_mem`, `phy_reg:iram1`, `wifi:set_pbus_mem_debug` |
| `0x600060F0` | `phy_pbus:save_pbus_reg`, `phy_pbus:set_pbus_mem`, `phy_reg:iram1`, `wifi:set_pbus_mem_debug` |
| `0x600060F4` | `phy_pbus:save_pbus_reg`, `phy_pbus:set_pbus_mem`, `phy_reg:iram1`, `wifi:set_pbus_mem_debug` |
| `0x600060FC` | `phy_reg:iram1` |
| `0x60006100` | `phy_reg:iram1` |
| `0x60006104` | `phy_pbus:ram_pbus_force_mode`, `phy_rx_cal:phy_force_rx_gain_trig` |
| `0x6000610C` | `phy_pbus:ram_pbus_force_mode`, `phy_reg:phy_close_pa` |
| `0x60006110` | `phy_basic:set_adc_rand`, `phy_init:iram1`, `phy_init:rf_init`, `phy_reg:force_txrx_off`, `phy_reg:phy_close_pa`, `phy_rx_gain:set_rx_gain_param` (+4) |
| `0x60006140` | `phy_rx_cal:ram_iq_est_enable` |
| `0x60006144` | `phy_rx_cal:ram_iq_est_enable` |
| `0x60006148` | `phy_analog_cal:phy_analog_delay_cal`, `phy_rx_cal:phy_2448m_spur_pwr`, `phy_rx_cal:rxiq_get_mis`, `phy_test:phy_corr_get_pwr`, `phy_test:rxiq_get_pwr`, `wifi:ram_get_corr_power` |
| `0x6000614C` | `phy_analog_cal:phy_analog_delay_cal`, `phy_rx_cal:phy_2448m_spur_pwr`, `phy_rx_cal:rxiq_get_mis`, `phy_test:phy_corr_get_pwr`, `phy_test:rxiq_get_pwr`, `wifi:ram_get_corr_power` |
| `0x60006150` | `phy_analog_cal:phy_analog_delay_cal`, `phy_rx_cal:phy_2448m_spur_pwr`, `phy_rx_cal:rxiq_get_mis`, `phy_test:phy_corr_get_pwr`, `phy_test:rxiq_get_pwr`, `wifi:ram_get_corr_power` |
| `0x60006154` | `phy_analog_cal:phy_analog_delay_cal`, `phy_rx_cal:phy_2448m_spur_pwr`, `phy_rx_cal:rxiq_get_mis`, `phy_test:phy_corr_get_pwr`, `phy_test:rxiq_get_pwr`, `wifi:ram_get_corr_power` |
| `0x6000615C` | `phy_rx_cal:set_rx_gain_cal_iq`, `wifi:dc_iq_est_test`, `wifi:ram_get_corr_power` |
| `0x60006160` | `phy_rx_cal:set_rx_gain_cal_iq`, `wifi:dc_iq_est_test`, `wifi:ram_get_corr_power` |
| `0x60006164` | `phy_rx_cal:phy_2448m_spur_pwr`, `phy_rx_cal:rxiq_get_mis`, `phy_rx_cal:set_rx_gain_cal_iq`, `wifi:dc_iq_est_test`, `wifi:get_iq_est_pwr`, `wifi:ram_get_corr_power` |
| `0x60006168` | `wifi:dc_iq_est_test` |
| `0x6000616C` | `wifi:dc_iq_est_test` |
| `0x60006174` | `phy_rx_cal:ram_iq_est_enable` |
| `0x60006180` | `phy_reg:iram1` |
| `0x60006184` | `phy_reg:iram1` |
| `0x60006188` | `phy_reg:iram1` |
| `0x6000618C` | `phy_reg:iram1` |
| `0x60006190` | `phy_reg:iram1` |
| `0x60006194` | `phy_reg:iram1` |
| `0x60006198` | `phy_reg:iram1` |
| `0x6000619C` | `phy_reg:iram1` |
| `0x600061A0` | `phy_reg:iram1` |
| `0x600061A4` | `phy_reg:iram1` |
| `0x600061A8` | `phy_reg:iram1` |
| `0x600061AC` | `phy_reg:iram1` |
| `0x600061B0` | `phy_reg:iram1` |
| `0x600061B4` | `phy_reg:iram1` |
| `0x600061B8` | `phy_reg:iram1` |
| `0x600061BC` | `phy_reg:iram1` |
| `0x600061C0` | `phy_init:iram1` |
| `0x600061D8` | `rf_test:cfr_set_reg` |
| `0x600061DC` | `rf_test:cfr_set_reg` |
| `0x600061E4` | `phy_reg:start_tx_tone_step`, `phy_reg:stop_tx_tone` |
| `0x600061FC` | `wifi:mem_read_func`, `wifi:mem_write_func`, `wifi:test_mem_time` |

## Baseband/AGC (0x6001_cxxx..0x6001_dxxx)

| Register | Used by |
|---|---|
| `0x6001C000` | `phy_debug:phy_reg_check` |
| `0x6001C010` | `phy_feature:set_rx_sense`, `phy_reg:iram1`, `phy_reg:phy_rx_sense_set` |
| `0x6001C014` | `phy_feature:set_rx_sense`, `phy_reg:iram1`, `phy_reg:phy_rx_sense_set` |
| `0x6001C018` | `bb_common:ate_txframe_dut`, `bb_common:beacon_print`, `bb_common:tx_ack_start`, `phy_reg:iram1`, `phy_reg:ram_check_noise_floor`, `phy_rx_cal:rom_noise_check_loop` |
| `0x6001C01C` | `phy_feature:phy_get_cca`, `phy_feature:set_rx_sense`, `phy_reg:iram1` |
| `0x6001C02C` | `mac_common:adctrig`, `phy_pbus:ram_pbus_force_mode`, `phy_reg:iram1`, `phy_reg:rom_agc_reg_init`, `phy_rx_cal:phy_force_rx_gain_trig`, `phy_rx_gain:set_rx_gain_table` (+4) |
| `0x6001C030` | `phy_feature:phy_11p_set`, `phy_reg:iram1` |
| `0x6001C034` | `phy_reg:iram1` |
| `0x6001C044` | `phy_feature:set_rx_sense`, `phy_reg:iram1`, `phy_reg:phy_rx_sense_set` |
| `0x6001C050` | `phy_feature:phy_get_noise_floor`, `phy_reg:ram_check_noise_floor`, `phy_rx_cal:rom_noise_check_loop`, `wifi:run_rftest_case` |
| `0x6001C05C` | `phy_reg:iram1`, `phy_reg:rom_agc_reg_init`, `phy_rx_cal:rfrx_sat_rst` |
| `0x6001C064` | `phy_reg:iram1` |
| `0x6001C068` | `phy_reg:iram1`, `phy_rx_cal:rfrx_sat_rst` |
| `0x6001C06C` | `bb_common:beacon_print`, `bb_common:do_rx_poll`, `phy_feature:phy_get_rssi` |
| `0x6001C074` | `phy_feature:phy_chan_filt_set`, `phy_reg:iram1` |
| `0x6001C080` | `phy_reg:iram1`, `phy_test:bt_rx_force` |
| `0x6001C08C` | `phy_reg:read_hw_noisefloor`, `phy_rx_cal:get_rfrx_sat`, `phy_rx_cal:phy_check_rx_sat`, `phy_rx_cal:ram_iq_est_enable` |
| `0x6001C094` | `phy_reg:rom_agc_reg_init` |
| `0x6001C0A4` | `phy_reg:rom_agc_reg_init`, `phy_rx_gain:set_rx_gain_table` |
| `0x6001C0CC` | `phy_feature:set_rx_sense`, `phy_reg:iram1` |
| `0x6001C0D0` | `phy_rx_gain:set_rx_gain_table` |
| `0x6001C0F4` | `phy_reg:wifi_rifs_mode_en` |
| `0x6001C104` | `phy_reg:iram1` |
| `0x6001C108` | `phy_reg:iram1`, `phy_reg:phy_rx_sense_set` |
| `0x6001C114` | `phy_reg:iram1` |
| `0x6001C11C` | `phy_feature:ant_btrx_cfg`, `phy_feature:ant_dft_cfg`, `phy_feature:ant_wifirx_cfg`, `phy_reg:iram1` |
| `0x6001C120` | `phy_reg:iram1` |
| `0x6001C124` | `phy_feature:set_rx_sense`, `phy_reg:iram1` |
| `0x6001C130` | `phy_rfpll:chip_v7_set_chan` |
| `0x6001C134` | `phy_reg:iram1` |
| `0x6001C13C` | `phy_reg:rom_agc_reg_init`, `phy_rx_gain:set_rx_gain_table` |
| `0x6001C1B0` | `phy_reg:iram1` |
| `0x6001C400` | `phy_api:phy_get_tx_seed`, `phy_api:phy_set_tx_seed`, `phy_basic:chan14_mic_cfg`, `phy_debug:phy_reg_check`, `phy_reg:ram_bb_reg_init`, `rf_test:wifitxout_func` (+2) |
| `0x6001C454` | `wifi:remove_11b_4p8G_spur` |
| `0x6001C458` | `wifi:WifiTxStart`, `wifi:remove_11b_4p8G_spur` |
| `0x6001C800` | `phy_debug:phy_reg_check` |
| `0x6001C804` | `phy_reg:iram1` |
| `0x6001C850` | `phy_reg:phy_freq_correct` |
| `0x6001C860` | `phy_feature:phy_disable_low_rate`, `phy_feature:phy_enable_low_rate`, `phy_feature:phy_rx11blr_cfg`, `phy_reg:iram1` |
| `0x6001C87C` | `phy_feature:phy_disable_low_rate`, `phy_feature:phy_enable_low_rate`, `phy_feature:phy_rx11blr_cfg`, `phy_reg:iram1` |
| `0x6001CC00` | `phy_debug:phy_reg_check`, `phy_reg:phy_fft_scale_force` |
| `0x6001CC0C` | `phy_reg:iram1` |
| `0x6001CC48` | `phy_reg:ram_bb_reg_init`, `phy_rx_cal:spur_coef_cfg_new` |
| `0x6001CC98` | `phy_reg:phy_freq_correct` |
| `0x6001CD04` | `phy_feature:phy_chan_filt_set`, `phy_reg:iram1`, `wifi:rftest_optimize` |
| `0x6001CD08` | `phy_feature:phy_chan_filt_set`, `phy_reg:iram1`, `wifi:rftest_optimize` |
| `0x6001CD0C` | `phy_feature:phy_chan_dump_cfg`, `phy_init:register_chipv7_phy`, `phy_reg:iram1` |
| `0x6001D000` | `phy_debug:phy_reg_check` |
| `0x6001D008` | `bb_common:tx_a_frame` |
| `0x6001D014` | `phy_rx_cal:spur_coef_cfg_new` |
| `0x6001D018` | `phy_rx_cal:spur_coef_cfg_new` |
| `0x6001D030` | `phy_reg:phy_freq_correct` |
| `0x6001D044` | `phy_rx_cal:rom_noise_check_loop` |
| `0x6001D050` | `phy_rx_cal:rom_noise_check_loop` |
| `0x6001D058` | `phy_feature:phy_set_cca_cnt` |
| `0x6001D05C` | `phy_feature:phy_get_cca_cnt` |
| `0x6001D060` | `phy_feature:phy_get_cca_cnt` |

## Modem (0x6002_6xxx)

| Register | Used by |
|---|---|
| `0x60026000` | `phy_debug:phy_reg_check` |
| `0x6002600C` | `phy_feature:phy_11p_set`, `phy_init:iram1`, `phy_pbus:ram_pbus_force_mode`, `phy_reg:iram1` |
| `0x60026010` | `phy_reg:iram1` |
| `0x60026014` | `wifi:phy_test_init`, `wifi:rftest_init` |
| `0x600260A8` | `rf_test:flash_test_init`, `rf_test:psram_test_init`, `wifi:test_cut_current` |

## MAC / dump engines (0x6003_3xxx..0x6003_5xxx)

| Register | Used by |
|---|---|
| `0x60033040` | `bb_common:ate_txframe_dut`, `bb_common:auto_ack_test`, `bb_common:fill_txaddr`, `bb_common:fill_txdataframe`, `bb_common:set_mac_filter`, `bb_common:setmacaddr` (+2) |
| `0x60033044` | `bb_common:ate_txframe_dut`, `bb_common:auto_ack_test`, `bb_common:fill_txaddr`, `bb_common:fill_txdataframe`, `bb_common:set_mac_filter`, `bb_common:setmacaddr` (+2) |
| `0x60033060` | `bb_common:set_mac_filter` |
| `0x60033064` | `bb_common:set_mac_filter` |
| `0x60033080` | `bb_common:ate_txframe_dut`, `bb_common:auto_ack_test`, `bb_common:get_rx_buffer`, `bb_common:get_rxctrl_addr`, `bb_common:tx_ack_init`, `mac_common:mac_init` |
| `0x60033084` | `bb_common:ate_txframe_dut`, `bb_common:auto_ack_test`, `bb_common:get_rx_buffer`, `bb_common:tx_ack_init`, `mac_common:rx_buffer_ena`, `mac_common:rx_buffer_init` (+1) |
| `0x60033088` | `mac_common:rx_ampdu_buffer_init`, `mac_common:rx_buffer_init` |
| `0x60033094` | `bb_common:auto_ack_test`, `bb_common:get_rxctrl_addr` |
| `0x600330A8` | `bb_common:beacon_print`, `bb_common:do_rx_poll` |
| `0x600330AC` | `bb_common:beacon_print`, `bb_common:do_rx_poll`, `bb_common:tx_ack_start` |
| `0x600330B0` | `bb_common:beacon_print`, `bb_common:do_rx_poll` |
| `0x600330D8` | `bb_common:ate_txframe_dut`, `bb_common:set_macrxfilter`, `mac_common:mac_init`, `wifi:rx_per_init` |
| `0x600330DC` | `bb_common:set_mac_filter`, `bb_common:set_macrxfilter`, `mac_common:mac_init`, `wifi:rx_per_init` |
| `0x600330E0` | `bb_common:set_mac_filter`, `bb_common:set_macrxfilter`, `mac_common:mac_init`, `wifi:rx_per_init` |
| `0x600330E4` | `bb_common:set_mac_filter`, `bb_common:set_macrxfilter`, `mac_common:mac_init`, `wifi:rx_per_init` |
| `0x60033404` | `bb_common:ack_rate_tab` |
| `0x60033408` | `bb_common:ack_rate_tab` |
| `0x6003340C` | `bb_common:ack_rate_tab` |
| `0x60033800` | `bb_common:beacon_print`, `bb_common:do_rx_poll`, `crypto_common:crypto_disable` |
| `0x60033814` | `crypto_common:crypto_disable` |
| `0x60033C04` | `bb_common:tx_data_frame`, `wifi:burnin_test_func` |
| `0x60033C18` | `mac_common:mac_init` |
| `0x60033C34` | `bb_common:trig_tx_frame`, `bb_common:tx_a_frame`, `bb_common:tx_data_frame`, `mac_common:mac_init`, `wifi:burnin_test_func` |
| `0x60033C3C` | `bb_common:beacon_print`, `bb_common:do_rx_poll`, `bb_common:trig_tx_frame`, `bb_common:tx_a_frame`, `bb_common:tx_data_frame`, `wifi:burnin_test_func` |
| `0x60033C40` | `bb_common:beacon_print`, `bb_common:do_rx_poll`, `bb_common:trig_tx_frame`, `bb_common:tx_a_frame`, `bb_common:tx_data_frame`, `wifi:burnin_test_func` |
| `0x60033C44` | `bb_common:ate_txframe_dut`, `bb_common:tx_ack_init` |
| `0x60033C64` | `bb_common:get_rxctrl_addr` |
| `0x60033C68` | `bb_common:tx_data_frame`, `rf_test:esp_tx_func_org`, `rf_test:wifitxout_func`, `wifi:burnin_test_func`, `wifi:run_rftest_case` |
| `0x60033C6C` | `mac_common:fill_tx_frame`, `mac_common:mac_init` |
| `0x60033C78` | `bb_common:ate_txframe_dut`, `bb_common:auto_ack_test`, `bb_common:tx_ack_init` |
| `0x60033CA0` | `bb_common:tx_a_frame` |
| `0x60033CA8` | `bb_common:tx_a_frame`, `wifi:WifiTxStart`, `wifi:burnin_test_func` |
| `0x60033CCC` | `mac_common:BackOffCountGet` |
| `0x60033CD4` | `mac_common:BackOffCountGet` |
| `0x60033CDC` | `mac_common:BackOffCountGet` |
| `0x60033CE4` | `mac_common:BackOffCountGet` |
| `0x60033CEC` | `mac_common:BackOffCountGet` |
| `0x60033CF4` | `mac_common:BackOffCountGet` |
| `0x60033CFC` | `mac_common:BackOffCountGet` |
| `0x60033D04` | `mac_common:BackOffCountGet`, `mac_common:ConfAddrGet`, `mac_common:mac_init`, `wifi:burnin_rtc_init` |
| `0x60033D08` | `mac_common:Plcp0AddrGet` |
| `0x60033D14` | `mac_common:mac_init` |
| `0x60033D5C` | `mac_common:adctrig`, `mac_common:dactrig` |
| `0x60033D60` | `mac_common:adctrig` |
| `0x60033D64` | `mac_common:dactrig` |
| `0x60033D84` | `bb_common:tx_a_frame` |
| `0x60033D90` | `mac_common:mac_init` |
| `0x600342F8` | `mac_common:Plcp1AddrGet` |
| `0x60034300` | `mac_common:HTsigAddrGet` |
| `0x60034310` | `mac_common:HT40LenAddrGet` |
| `0x60034318` | `mac_common:DurAddrGet` |
| `0x60035000` | `bb_common:ate_txframe_dut`, `bb_common:beacon_print`, `bb_common:do_rx_poll`, `bb_common:get_rx_buffer`, `bb_common:test_tx_frame`, `bb_common:trig_tx_frame` (+12) |
| `0x60035004` | `bb_common:beacon_print`, `bb_common:do_rx_poll` |
| `0x6003507C` | `bb_common:ate_txframe_dut`, `bb_common:fill_txdataframe`, `bb_common:tx_ack_start` |
| `0x6003509C` | `phy_hw_freq:set_chan_freq_hw_init` |
