from django import forms


class BaseTotalForm(forms.Form):
    base_name = forms.CharField(max_length=64, widget=forms.HiddenInput())
    rw_total = forms.IntegerField(min_value=0, required=False, initial=0, label="RW")
    gr_total = forms.IntegerField(min_value=0, required=False, initial=0, label="GR")


BaseTotalsFormSet = forms.formset_factory(BaseTotalForm, extra=0)


class WeekForm(forms.Form):
    week_start = forms.CharField(max_length=10, label="Week start (Sunday YYYY-MM-DD)")
    filled_day = forms.IntegerField(min_value=0, initial=52, label="Day")
    filled_night = forms.IntegerField(min_value=0, initial=30, label="Night")
    ot_rn_day = forms.IntegerField(
        min_value=0, required=False, initial=0, label="OT RN (Day)"
    )
    ot_rn_night = forms.IntegerField(
        min_value=0, required=False, initial=0, label="OT RN (Night)"
    )
    ot_medic_day = forms.IntegerField(
        min_value=0, required=False, initial=0, label="OT Medic (Day)"
    )
    ot_medic_night = forms.IntegerField(
        min_value=0, required=False, initial=0, label="OT Medic (Night)"
    )
    ot_emt_day = forms.IntegerField(
        min_value=0, required=False, initial=0, label="OT EMT (Day)"
    )
    ot_emt_night = forms.IntegerField(
        min_value=0, required=False, initial=0, label="OT EMT (Night)"
    )
    leave_at = forms.IntegerField(
        min_value=0, required=False, initial=0, label="Leave AT"
    )
    leave_lt = forms.IntegerField(
        min_value=0, required=False, initial=0, label="Leave LT"
    )
    leave_sick = forms.IntegerField(
        min_value=0, required=False, initial=0, label="Leave SICK"
    )
    leave_loa = forms.IntegerField(
        min_value=0, required=False, initial=0, label="Leave LOA"
    )
    leave_jury = forms.IntegerField(
        min_value=0, required=False, initial=0, label="Leave JURY"
    )
    leave_brev = forms.IntegerField(
        min_value=0, required=False, initial=0, label="Leave BREV (Bereavement)"
    )
    medic_unpartnered = forms.IntegerField(
        min_value=0,
        required=False,
        initial=0,
        label="Medic unpartnered",
        help_text="Manual; appears on Weekly Detail export.",
    )
    rn_unpartnered_staff = forms.IntegerField(
        min_value=0,
        required=False,
        initial=0,
        label="RN unpartnered staff",
        help_text="Manual; appears on Weekly Detail export.",
    )
    unpartnered_note_medic = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "maxlength": "200"}
        ),
        label="Why unpartnered (Medic)",
        help_text="Short note in Weekly Detail (Notes column).",
    )
    unpartnered_note_rn = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-sm", "maxlength": "200"}
        ),
        label="Why unpartnered (RN)",
        help_text="Short note in Weekly Detail (Notes column).",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "cols": 50}),
        label="Notes",
    )


class BaseCoverageForm(forms.Form):
    base_name = forms.CharField(max_length=64, widget=forms.HiddenInput())
    rw_staffed_day = forms.IntegerField(
        min_value=0, required=False, initial=0, label="RW day"
    )
    rw_staffed_night = forms.IntegerField(
        min_value=0, required=False, initial=0, label="RW night"
    )
    gr_staffed_day = forms.IntegerField(
        min_value=0, required=False, initial=0, label="GR day"
    )
    gr_staffed_night = forms.IntegerField(
        min_value=0, required=False, initial=0, label="GR night"
    )


BaseCoverageFormSet = forms.formset_factory(BaseCoverageForm, extra=0)


# KPI metrics stored as fractions in DB but edited as 0–100 in the UI.
PERCENT_KPI_METRICS = frozenset(
    {
        "Staffing Rate",
        "OT Dependency",
        "Shift Exception %",
        "System RW Coverage %",
        "System GR Coverage %",
    }
)


class KpiThresholdRowForm(forms.Form):
    metric_name = forms.CharField(widget=forms.HiddenInput())
    green_min = forms.FloatField(
        required=False,
        label="Green min",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "any"}),
    )
    green_max = forms.FloatField(
        required=False,
        label="Green max",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "any"}),
    )
    yellow_min = forms.FloatField(
        required=False,
        label="Yellow min",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "any"}),
    )
    yellow_max = forms.FloatField(
        required=False,
        label="Yellow max",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "any"}),
    )
    red_min = forms.FloatField(
        required=False,
        label="Red min",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "any"}),
    )
    red_max = forms.FloatField(
        required=False,
        label="Red max",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "any"}),
    )
    higher_is_better = forms.BooleanField(
        required=False,
        label="Higher is better",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


KpiThresholdFormSet = forms.formset_factory(KpiThresholdRowForm, extra=0)


class ManagerRosterAddForm(forms.Form):
    last_name = forms.CharField(
        max_length=128,
        label="Last name",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. Ender", "autocomplete": "off"}
        ),
    )

    def clean_last_name(self):
        name = (self.cleaned_data.get("last_name") or "").strip()
        if not name:
            raise forms.ValidationError("Enter a last name.")
        return name.title()


STAFF_ROSTER_ROLE_CHOICES = (
    ("RN", "RN"),
    ("MEDIC", "Medic"),
    ("EMT", "EMT"),
)


class StaffRosterAddForm(forms.Form):
    last_name = forms.CharField(
        max_length=128,
        label="Last name",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. Smith", "autocomplete": "off"}
        ),
    )
    first_name = forms.CharField(
        max_length=128,
        required=False,
        label="First name (optional)",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. Jane", "autocomplete": "off"}
        ),
    )
    role = forms.ChoiceField(
        choices=STAFF_ROSTER_ROLE_CHOICES,
        label="Role",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def clean_last_name(self):
        name = (self.cleaned_data.get("last_name") or "").strip()
        if not name:
            raise forms.ValidationError("Enter a last name.")
        return name.title()

    def clean_first_name(self):
        name = (self.cleaned_data.get("first_name") or "").strip()
        return name.title() if name else ""
