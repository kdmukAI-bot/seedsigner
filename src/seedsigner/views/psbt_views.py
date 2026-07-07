from seedsigner.compat.l10n import gettext as _
from seedsigner.compat.l10n import ngettext

from seedsigner.models.psbt_parser import PSBTParser
from seedsigner.models.settings import SettingsConstants
from seedsigner.gui.constants import FontAwesomeIconConstants, GUIConstants, SeedSignerIconConstants, StatusType
from seedsigner.views.view import (BackStackView, ButtonOption, Destination, MainMenuView,
    NotYetImplementedView, RET_CODE__BACK_BUTTON, View)



class PSBTSelectSeedView(View):
    SCAN_SEED = ButtonOption("Scan a seed", SeedSignerIconConstants.QRCODE)
    TYPE_12WORD = ButtonOption("Enter 12-word seed", FontAwesomeIconConstants.KEYBOARD)
    TYPE_24WORD = ButtonOption("Enter 24-word seed", FontAwesomeIconConstants.KEYBOARD)
    TYPE_ELECTRUM = ButtonOption("Enter Electrum seed", FontAwesomeIconConstants.KEYBOARD)


    def run(self):
        from seedsigner.controller import Controller
        # Note: we can't just autoroute to the PSBT Overview because we might have a
        # multisig where we want to sign with more than one key on this device.
        if not self.controller.psbt:
            # Shouldn't be able to get here
            raise Exception("No transaction currently loaded")

        if self.controller.psbt_seed:
             if PSBTParser.has_matching_input_fingerprint(psbt=self.controller.psbt, seed=self.controller.psbt_seed, network=self.settings.get_value(SettingsConstants.SETTING__NETWORK)):
                 # skip the seed prompt if a seed was previously selected and has matching input fingerprint
                 return Destination(PSBTOverviewView)

        seeds = self.controller.storage.seeds
        button_data = []
        for seed in seeds:
            button_str = seed.get_fingerprint(self.settings.get_value(SettingsConstants.SETTING__NETWORK))
            if not PSBTParser.has_matching_input_fingerprint(psbt=self.controller.psbt, seed=seed, network=self.settings.get_value(SettingsConstants.SETTING__NETWORK)):
                # Doesn't look like this seed can sign the current PSBT
                # TRANSLATOR_NOTE: Inserts fingerprint w/"?" to indicate that this seed can't sign the current PSBT
                button_str = _("{} (?)").format(button_str)

            button_data.append(ButtonOption(button_str, SeedSignerIconConstants.FINGERPRINT))

        button_data.append(self.SCAN_SEED)
        button_data.append(self.TYPE_12WORD)
        button_data.append(self.TYPE_24WORD)
        if self.settings.get_value(SettingsConstants.SETTING__ELECTRUM_SEEDS) == SettingsConstants.OPTION__ENABLED:
            button_data.append(self.TYPE_ELECTRUM)

        selected_menu_num = self.run_button_list_screen(
            title=_("Select Signer"),
            is_button_text_centered=False,
            button_data=button_data
        )

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)

        if len(seeds) > 0 and selected_menu_num < len(seeds):
            # User selected one of the n seeds
            self.controller.psbt_seed = self.controller.get_seed(selected_menu_num)
            return Destination(PSBTOverviewView)
        
        # The remaining flows are a sub-flow; resume PSBT flow once the seed is loaded.
        self.controller.resume_main_flow = Controller.FLOW__PSBT

        if button_data[selected_menu_num] == self.SCAN_SEED:
            from seedsigner.views.scan_views import ScanSeedQRView
            return Destination(ScanSeedQRView)

        elif button_data[selected_menu_num] in [self.TYPE_12WORD, self.TYPE_24WORD]:
            from seedsigner.views.seed_views import SeedMnemonicEntryView
            if button_data[selected_menu_num] == self.TYPE_12WORD:
                self.controller.storage.init_pending_mnemonic(num_words=12)
            else:
                self.controller.storage.init_pending_mnemonic(num_words=24)
            return Destination(SeedMnemonicEntryView)

        elif button_data[selected_menu_num] == self.TYPE_ELECTRUM:
            from seedsigner.views.seed_views import SeedElectrumMnemonicStartView
            return Destination(SeedElectrumMnemonicStartView)



class PSBTOverviewView(View):
    def __init__(self):
        super().__init__()

        if not self.controller.psbt_parser or self.controller.psbt_parser.seed != self.controller.psbt_seed:
            # The PSBTParser takes a while to read the PSBT. Show the loading spinner while
            # we wait; fire-and-forget — the next screen's run_screen tears it down (an error
            # mid-parse propagates and the error View's run_screen does the teardown).
            from seedsigner.gui.lvgl_screen_runner import run_loading_screen
            run_loading_screen(_("Parsing PSBT..."))

            self.controller.psbt_parser = PSBTParser(
                self.controller.psbt,
                seed=self.controller.psbt_seed,
                network=self.settings.get_value(SettingsConstants.SETTING__NETWORK)
            )


    def run(self):
        from seedsigner.gui.lvgl_config import btc_amount_from_sats
        psbt_parser = self.controller.psbt_parser

        change_data = psbt_parser.change_data
        """
            change_data = [
                {
                    'address': 'bc1q............',
                    'amount': 397621401,
                    'fingerprint': ['22bde1a9', '73c5da0a'],
                    'derivation_path': ['m/48h/1h/0h/2h/1/0', 'm/48h/1h/0h/2h/1/0']
                }, {},
            ]
        """
        num_change_outputs = 0
        num_self_transfer_outputs = 0
        for change_output in change_data:
            # print(f"""{change_output["derivation_path"][0]}""")
            if change_output["derivation_path"][0].split("/")[-2] == "1":
                num_change_outputs += 1
            else:
                num_self_transfer_outputs += 1

        # Headline amount: the spend on a normal send, or the change on a self-transfer
        # (no external recipients), matching the PIL overview's callout.
        if not psbt_parser.destination_addresses:
            headline_sats = psbt_parser.change_amount
        else:
            headline_sats = psbt_parser.spend_amount

        # Translated labels for the transaction-flow pictogram (generic structural words —
        # it conveys structure, not addresses). Each key falls back to English natively if
        # omitted, so this is purely the i18n pass; the native screen owns the layout.
        labels = {
            # TRANSLATOR_NOTE: Single-input transaction label in the tx-flow diagram
            "one_input": _("1 input"),
            # TRANSLATOR_NOTE: Input number will be inserted (e.g. "input 3")
            "input_n": _("input {}"),
            # TRANSLATOR_NOTE: Indicates that items have been omitted from a series: e.g. "1, 2, 3, [...], 8"
            "ellipsis_series": _("[ ... ]"),
            # TRANSLATOR_NOTE: Inserts the recipient number (e.g. the fifth one is: "recipient 5")
            "recipient_n": _("recipient {}"),
            "recipient_1": _("recipient 1"),
            "recipient_singular": _("recipient"),
            "self_transfer": _("self-transfer"),
            "fee": _("fee"),
            # TRANSLATOR_NOTE: Technical term, should probably NOT be translated in most languages
            "op_return": _("OP_RETURN"),
            # TRANSLATOR_NOTE: Label for a change output in the PSBT Overview flow diagram
            "change": _("change"),
        }

        # Run the overview screen (its run_screen tears down the loading spinner).
        selected_menu_num = self.run_screen(
            "psbt_overview_screen",
            title=_("Review Transaction"),
            button_data=[ButtonOption("Review details")],
            btc_amount=btc_amount_from_sats(headline_sats),
            num_inputs=psbt_parser.num_inputs,
            num_self_transfer_outputs=num_self_transfer_outputs,
            num_change_outputs=num_change_outputs,
            destination_addresses=psbt_parser.destination_addresses,
            has_op_return=psbt_parser.op_return_data is not None,
            labels=labels,
        )

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            self.controller.psbt_seed = None
            return Destination(BackStackView)

        # expecting p2sh (legacy multisig) and p2pkh to have no policy set
        # skip change warning and psbt math view
        if psbt_parser.policy == None:
            return Destination(PSBTUnsupportedScriptTypeWarningView)
        
        elif psbt_parser.change_amount == 0:
            return Destination(PSBTNoChangeWarningView)

        else:
            return Destination(PSBTMathView)



class PSBTUnsupportedScriptTypeWarningView(View):
    def run(self):
        selected_menu_num = self.run_status_screen(
            status_type=StatusType.WARNING,
            status_headline=_("Unsupported Script Type!"),
            text=_("Transaction has unsupported input script type, please verify your change addresses."),
            button_data=[ButtonOption("Continue")],
        )
        
        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)
        
        # Only one exit point
        # skip PSBTMathView
        return Destination(
            PSBTAddressDetailsView, view_args={"address_num": 0},
            skip_current_view=True,  # Prevent going BACK to WarningViews
        )



class PSBTNoChangeWarningView(View):
    def run(self):
        selected_menu_num = self.run_status_screen(
            status_type=StatusType.WARNING,
            # TRANSLATOR_NOTE: User will receive no change back; the inputs to this transaction are fully spent
            status_headline=_("Full Spend!"),
            text=_("This transaction spends its entire input value. No change is coming back to your wallet."),
            button_data=[ButtonOption("Continue")],
        )

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)

        # Only one exit point
        return Destination(
            PSBTMathView,
            skip_current_view=True,  # Prevent going BACK to WarningViews
        )



class PSBTMathView(View):
    """
        Follows the Overview pictogram. Shows:
        + total input value
        - recipients' value
        - fees
        -------------------
        + change value
    """
    def run(self):
        from seedsigner.gui.lvgl_config import format_btc, format_sats
        psbt_parser: PSBTParser = self.controller.psbt_parser
        if not psbt_parser:
            # Should not be able to get here
            return Destination(MainMenuView)

        num_recipients = psbt_parser.num_destinations

        # Denomination + host-formatted (integer-math) amount strings. The native screen
        # right-aligns and pads them into the fee equation, so we pass them UNPADDED. Btc
        # mode keeps the fee in sats (parity with the PIL math screen).
        if psbt_parser.input_amount > 1_000_000:
            denomination = "btc"
            unit_word = _("btc")
            amounts = {
                "input": format_btc(psbt_parser.input_amount),
                "spend": format_btc(psbt_parser.spend_amount),
                "fee": str(psbt_parser.fee_amount),
                "change": format_btc(psbt_parser.change_amount),
            }
        else:
            denomination = "sats"
            unit_word = _("sats")
            amounts = {
                "input": format_sats(psbt_parser.input_amount),
                "spend": format_sats(psbt_parser.spend_amount),
                "fee": format_sats(psbt_parser.fee_amount),
                "change": format_sats(psbt_parser.change_amount),
            }

        # Already-localized (and pluralized) info words drawn after each amount.
        labels = {
            "inputs": ngettext("input", "inputs", psbt_parser.num_inputs),
            "recipients": ngettext("recipient", "recipients", num_recipients),
            "fee": _("fee"),
            # TRANSLATOR_NOTE: Denomination is inserted (e.g. your "btc change" or "sats change")
            "change": _("{} change").format(unit_word),
        }

        selected_menu_num = self.run_screen(
            "psbt_math_screen",
            title=_("Transaction Math"),
            button_data=[ButtonOption("Review recipients")],
            denomination=denomination,
            num_recipients=num_recipients,
            amounts=amounts,
            labels=labels,
        )

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)

        if len(psbt_parser.destination_addresses) > 0:
            return Destination(PSBTAddressDetailsView, view_args={"address_num": 0})
        else:
            # This is a self-transfer
            return Destination(PSBTChangeDetailsView, view_args={"change_address_num": 0})



class PSBTAddressDetailsView(View):
    """
        Shows the recipient's address and amount they will receive
    """
    def __init__(self, address_num):
        super().__init__()
        self.address_num = address_num


    def run(self):
        from seedsigner.gui.lvgl_config import btc_amount_from_sats
        psbt_parser: PSBTParser = self.controller.psbt_parser

        if not psbt_parser:
            # Should not be able to get here
            raise Exception("Routing error")

        # TRANSLATOR_NOTE: Future-tense used to indicate that this transaction will send this amount, as opposed to "Send" on its own which could be misread as an instant command (e.g. "Send Now").
        title = _("Will Send")
        if psbt_parser.num_destinations > 1:
            title += f" (#{self.address_num + 1})"

        button_data = []
        if self.address_num < psbt_parser.num_destinations - 1:
            button_data.append(ButtonOption("Next recipient"))
        else:
            # TRANSLATOR_NOTE: Short for "Next step"
            button_data.append(ButtonOption("Next"))

        selected_menu_num = self.run_screen(
            "psbt_address_details_screen",
            title=title,
            button_data=button_data,
            address=psbt_parser.destination_addresses[self.address_num],
            btc_amount=btc_amount_from_sats(psbt_parser.destination_amounts[self.address_num]),
        )
        
        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)

        if self.address_num < len(psbt_parser.destination_addresses) - 1:
            # Show the next receive addr
            return Destination(PSBTAddressDetailsView, view_args={"address_num": self.address_num + 1})

        elif psbt_parser.change_amount > 0:
            # Move on to display change
            return Destination(PSBTChangeDetailsView, view_args={"change_address_num": 0})

        elif psbt_parser.op_return_data:
            return Destination(PSBTOpReturnView)

        else:
            # There's no change output to verify. Move on to sign the PSBT.
            return Destination(PSBTFinalizeView)



class PSBTChangeDetailsView(View):
    NEXT = ButtonOption("Next")
    SKIP_VERIFICATION = ButtonOption("Skip verification")
    VERIFY_MULTISIG = ButtonOption("Verify multisig change")

    def __init__(self, change_address_num):
        super().__init__()
        self.change_address_num = change_address_num


    def run(self):
        from seedsigner.gui.lvgl_config import btc_amount_from_sats
        psbt_parser: PSBTParser = self.controller.psbt_parser

        if not psbt_parser:
            # Should not be able to get here
            return Destination(MainMenuView)

        # Can we verify this change addr?
        change_data = psbt_parser.get_change_data(change_num=self.change_address_num)
        """
            change_data:
            {
                'address': 'bc1q............', 
                'amount': 397621401, 
                'fingerprint': ['22bde1a9', '73c5da0a'], 
                'derivation_path': ['m/48h/1h/0h/2h/1/0', 'm/48h/1h/0h/2h/1/0']
            }
        """

        # Single-sig verification is easy. We expect to find a single fingerprint
        # and derivation path.
        seed_fingerprint = self.controller.psbt_seed.get_fingerprint(self.settings.get_value(SettingsConstants.SETTING__NETWORK))

        if seed_fingerprint not in change_data.get("fingerprint"):
            # TODO: Something is wrong with this psbt(?). Reroute to warning?
            return Destination(NotYetImplementedView)

        i = change_data.get("fingerprint").index(seed_fingerprint)
        derivation_path = change_data.get("derivation_path")[i]

        # 'm/84h/1h/0h/1/0' would be a change addr while 'm/84h/1h/0h/0/0' is a self-receive
        is_change_derivation_path = int(derivation_path.split("/")[-2]) == 1
        derivation_path_addr_index = int(derivation_path.split("/")[-1])

        if is_change_derivation_path:
            # TRANSLATOR_NOTE: The amount you're receiving back from the transaction
            title = _("Your Change")
        else:
            title = _("Self-Transfer")
            self.VERIFY_MULTISIG.button_label = _("Verify multisig addr")
        # if psbt_parser.num_change_outputs > 1:
        #     title += f" (#{self.change_address_num + 1})"

        is_change_addr_verified = False
        if psbt_parser.is_multisig:
            # if the known-good multisig descriptor is already onboard:
            if self.controller.multisig_wallet_descriptor:
                is_change_addr_verified = psbt_parser.verify_multisig_output(self.controller.multisig_wallet_descriptor, change_num=self.change_address_num)
                button_data = [self.NEXT]

            else:
                # Have the Screen offer to load in the multisig descriptor.            
                button_data = [self.VERIFY_MULTISIG, self.SKIP_VERIFICATION]

        else:
            # Single sig
            from embit import script
            from embit.networks import NETWORKS

            if is_change_derivation_path:
                loading_screen_text = _("Verifying Change...")
            else:
                loading_screen_text = _("Verifying Self-Transfer...")
            # Fire-and-forget spinner; the next screen's run_screen tears it down.
            from seedsigner.gui.lvgl_screen_runner import run_loading_screen
            run_loading_screen(loading_screen_text)

            # convert change address to script pubkey to get script type
            pubkey = script.address_to_scriptpubkey(change_data["address"])
            script_type = pubkey.script_type()

            # extract derivation path to get wallet and change derivation
            change_path = '/'.join(derivation_path.split("/")[-2:])
            wallet_path = '/'.join(derivation_path.split("/")[:-2])

            xpub = self.controller.psbt_seed.get_xpub(
                wallet_path=wallet_path,
                network=self.settings.get_value(SettingsConstants.SETTING__NETWORK)
            )

            # take script type and call script method to generate address from seed / derivation
            xpub_key = xpub.derive(change_path).key
            network = self.settings.get_value(SettingsConstants.SETTING__NETWORK)
            scriptcall = getattr(script, script_type)
            if script_type == "p2sh":
                # single sig only so p2sh is always p2sh-p2wpkh
                calc_address = script.p2sh(script.p2wpkh(xpub_key)).address(
                    network=NETWORKS[SettingsConstants.map_network_to_embit(network)]
                )
            else:
                # single sig so this handles p2wpkh and p2wpkh (and p2tr in the future)
                calc_address = scriptcall(xpub_key).address(
                    network=NETWORKS[SettingsConstants.map_network_to_embit(network)]
                )

            if change_data["address"] == calc_address:
                is_change_addr_verified = True
                button_data = [self.NEXT]

        if is_change_addr_verified == False and (not psbt_parser.is_multisig or self.controller.multisig_wallet_descriptor is not None):
            return Destination(PSBTAddressVerificationFailedView, view_args=dict(is_change=is_change_derivation_path, is_multisig=psbt_parser.is_multisig), clear_history=True)

        if is_change_derivation_path:
            # TRANSLATOR_NOTE: Describes the address type (change or receive)
            addr_type = _("change address")
        else:
            addr_type = _("receive address")
        # TRANSLATOR_NOTE: Symbol for index number, e.g. "address #3"
        index_num_symbol = _("#")
        # note: NOT marking this for translation, hoping that the var ordering will not
        # need to change in other languages.
        address_type_label = f"{addr_type} {index_num_symbol}{derivation_path_addr_index}"

        selected_menu_num = self.run_screen(
            "psbt_change_details_screen",
            title=title,
            button_data=button_data,
            address=change_data.get("address"),
            btc_amount=btc_amount_from_sats(change_data.get("amount")),
            address_type_label=address_type_label,
            is_verified=is_change_addr_verified,
            verified_text=_("Address verified!"),
        )

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)

        elif button_data[selected_menu_num] == self.NEXT or button_data[selected_menu_num] == self.SKIP_VERIFICATION:
            if self.change_address_num < psbt_parser.num_change_outputs - 1:
                return Destination(PSBTChangeDetailsView, view_args={"change_address_num": self.change_address_num + 1})

            elif psbt_parser.op_return_data:
                return Destination(PSBTOpReturnView)

            else:
                # There's no more change to verify. Move on to sign the PSBT.
                return Destination(PSBTFinalizeView)
            
        elif button_data[selected_menu_num] == self.VERIFY_MULTISIG:
            from seedsigner.controller import Controller
            from seedsigner.views.seed_views import LoadMultisigWalletDescriptorView
            self.controller.resume_main_flow = Controller.FLOW__PSBT
            return Destination(LoadMultisigWalletDescriptorView)
            


class PSBTAddressVerificationFailedView(View):
    def __init__(self, is_change: bool = True, is_multisig: bool = False):
        super().__init__()
        self.is_change = is_change
        self.is_multisig = is_multisig


    def run(self):
        if self.is_multisig:
            # TRANSLATOR_NOTE: Variable is either "change" or "self-transfer".
            text = _("Transaction's {} address could not be verified from wallet descriptor.").format(_("change") if self.is_change else _("self-transfer"))
        else:
            # TRANSLATOR_NOTE: Variable is either "change" or "self-transfer".
            text = _("Transaction's {} address could not be generated from your seed.").format(_("change") if self.is_change else _("self-transfer"))
        
        self.run_status_screen(
            status_type=StatusType.DIRE_WARNING,
            title=_("Suspicious Transaction"),
            show_back_button=False,
            status_headline=_("Address Verification Failed"),
            text=text,
            button_data=[ButtonOption("Discard transaction")],
        )

        # We're done with this PSBT. Route back to MainMenuView which always
        #   clears all ephemeral data (except in-memory seeds).
        return Destination(MainMenuView, clear_history=True)



class PSBTOpReturnView(View):
    """
        Shows the OP_RETURN data
    """
    def run(self):
        from seedsigner.gui.screens.psbt_screens import PSBTOpReturnScreen
        psbt_parser: PSBTParser = self.controller.psbt_parser

        if not psbt_parser:
            # Should not be able to get here
            raise Exception("Routing error")

        title = _("OP_RETURN")
        button_data = [ButtonOption("Next")]

        selected_menu_num = self.run_screen(
            PSBTOpReturnScreen,
            title=title,
            button_data=button_data,
            op_return_data=psbt_parser.op_return_data,
        )
        
        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)

        return Destination(PSBTFinalizeView)



class PSBTFinalizeView(View):
    """
    """
    APPROVE_PSBT = ButtonOption("Approve transaction")

    
    def run(self):
        from embit.psbt import PSBT

        psbt_parser: PSBTParser = self.controller.psbt_parser
        psbt: PSBT = self.controller.psbt

        if not psbt_parser:
            # Should not be able to get here
            return Destination(MainMenuView)

        # Custom large-icon status screen: the SIGN glyph in the info color over a
        # click-to-approve prompt (native parity with the PIL PSBTFinalizeScreen).
        selected_menu_num = self.run_status_screen(
            status_type=StatusType.CUSTOM,
            icon=SeedSignerIconConstants.SIGN,
            icon_color=GUIConstants.INFO_COLOR,
            title=_("Sign Transaction"),
            text=_("Click to approve this transaction"),
            button_data=[self.APPROVE_PSBT],
        )

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)

        else:
            # Sign PSBT
            sig_cnt = PSBTParser.sig_count(psbt)
            psbt.sign_with(psbt_parser.root)
            trimmed_psbt = PSBTParser.trim(psbt)

            if sig_cnt == PSBTParser.sig_count(trimmed_psbt):
                # Signing failed / didn't do anything
                # TODO: Reserved for Nick. Are there different failure scenarios that we can detect?
                # Would be nice to alter the message on the next screen w/more detail.
                return Destination(PSBTSigningErrorView)
            
            else:
                self.controller.psbt = trimmed_psbt
                return Destination(PSBTSignedQRDisplayView)



class PSBTSignedQRDisplayView(View):
    def run(self):
        from seedsigner.models.encode_qr import UrPsbtQrEncoder

        qr_encoder = UrPsbtQrEncoder(
            psbt=self.controller.psbt,
            qr_density=self.settings.get_value(SettingsConstants.SETTING__QR_DENSITY),
        )
        self.run_qr_display_screen(qr_encoder=qr_encoder)

        # We're done with this PSBT. Route back to MainMenuView which always
        #   clears all ephemeral data (except in-memory seeds).
        return Destination(MainMenuView, clear_history=True)



class PSBTSigningErrorView(View):
    SELECT_DIFF_SEED = ButtonOption("Select different seed")
    
    def run(self):
        psbt_parser: PSBTParser = self.controller.psbt_parser
        if not psbt_parser:
            # Should not be able to get here
            return Destination(MainMenuView)

        # Just a warning here; only use dire_warning for true security risks.
        selected_menu_num = self.run_status_screen(
            status_type=StatusType.WARNING,
            title=_("Transaction Error"),
            status_headline=_("Signing Failed"),
            text=_("Signing with this seed did not add a valid signature."),
            button_data=[self.SELECT_DIFF_SEED],
        )

        if selected_menu_num == 0:
            # clear seed selected for psbt signing since it did not add a valid signature
            self.controller.psbt_seed = None
            return Destination(PSBTSelectSeedView, clear_history=True)

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)
